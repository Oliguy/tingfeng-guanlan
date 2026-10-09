"""Bounded read-only quote references. No collectors, network or persistent price cache."""
from guanlan_data.config import source_path
from guanlan_data.layout import same_source_stamp
import hashlib
import json
import math
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import iso
from guanlan_domain.industry_index.indicators import calculator

VALUES=('open','high','low','close','adj_factor','vol_lot','amount_thousand_cny')
ROW_FIELDS=('trade_date','open','high','low','close','vol_lot','amount_thousand_cny','adj_factor')
_scope = ContextVar('observer_quote_read_scope', default=None)

class _ReadScope:
    """Request-local connections and verified evidence, never a price cache."""
    def __init__(self):
        self.stack=ExitStack();self.connections={};self.calendars={};self.receipts={};self.sources={}
        self.stamps={};self.calendar_snapshots={}

    def calendar_snapshot(self,ref):
        from guanlan_data.repositories.observer_calendar import load as load_calendar; from guanlan_data.repositories.observer_calendar import source_stamp
        path=source_path(ref['market_path']);stamp=source_stamp(path)
        cached=self.calendar_snapshots.get(path)
        if cached and cached[0]!=stamp:raise ValueError('读取期间日历来源发生变化，请重试')
        if not cached:
            cached=(stamp,load_calendar(path,connection=self.connection(path)))
            self.calendar_snapshots[path]=cached
        return cached[1]

    def connection(self,path):
        path=str(source_path(path))
        stamp=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.is_file() else None
                    for p in (Path(path),Path(path+'-wal')))
        if path in self.stamps and stamp!=self.stamps[path]:
            raise ValueError('读取期间行情来源发生变化，请重试')
        if path not in self.connections:
            self.stamps[path]=stamp
            self.connections[path]=self.stack.enter_context(readonly(path))
        return self.connections[path]

    def calendar(self,ref):
        key=(ref['market_path'],ref['start'],ref['end'])
        if key not in self.calendars:
            self.calendars[key]=list(self.calendar_snapshot(ref).sessions(ref['start'],ref['end']))
        return self.calendars[key]

    def receipt(self,path,key,checksum,code):
        self.connection(path)  # Also fence changes before reusing a verified receipt.
        cache_key=(str(Path(path).resolve()),key,checksum)
        if cache_key not in self.receipts:
            record=self.connection(path).execute('SELECT endpoint,rows,checksum FROM responses WHERE request_id=?',(key,)).fetchone()
            if not record:raise ValueError('补充行情来源已丢失，请本地重算')
            values=json.loads(record['rows'])
            if record['checksum']!=checksum or digest(values)!=checksum:
                raise ValueError('补充行情来源已修订，请本地重算')
            by_code={}
            for row in values:by_code.setdefault(row.get('ts_code'),[]).append(row)
            self.receipts[cache_key]=(record['endpoint'],by_code)
        endpoint,by_code=self.receipts[cache_key]
        return endpoint,by_code.get(code,())

@contextmanager
def read_scope():
    """Reuse sources within one member projection; release them on every exit."""
    existing=_scope.get()
    if existing is not None:
        yield existing;return
    scope=_ReadScope();token=_scope.set(scope)
    try:yield scope
    finally:
        _scope.reset(token);scope.stack.close()

def fingerprint(rows,metadata,sessions):
    # Semantic values, not fetch timestamps or provider serialization hashes.
    return digest({'rows':[[r['trade_date'],*[float(r[k]) if r.get(k) is not None else None for k in VALUES]] for r in rows],
                   'identity':{k:(metadata or {}).get(k) for k in ('list_status','list_date')},'sessions':sessions})

def daily_volume_ratio(rows,sessions,suspensions=None):
    """Close-of-day volume / mean of the five preceding open sessions."""
    day=sessions[-1] if sessions else None
    result={'volume_ratio':None,'volume_ratio_date':day,
            'volume_ratio_reason':'前5个交易日行情不足或缺失'}
    if len(sessions)<6:return result
    by_day={r['trade_date']:r.get('vol_lot') for r in rows if r['trade_date'] in sessions[-6:]}
    volumes=[]
    for session in sessions[-6:]:
        volume=by_day.get(session,0 if session in (suspensions or {}) else None)
        if not isinstance(volume,(int,float)) or isinstance(volume,bool) or not math.isfinite(volume) or volume<0:
            return result
        volumes.append(volume)
    average=sum(volumes[:-1])/5
    if average<=0:
        result['volume_ratio_reason']='前5个交易日平均成交量为零';return result
    result.update(volume_ratio=volumes[-1]/average,volume_ratio_reason=None)
    return result

def summary(rows,*,sessions=None,suspensions=None):
    last=rows[-1] if rows else {};prev=rows[-2] if len(rows)>1 else {}
    denominator=(prev.get('close') or 0)*(prev.get('adj_factor') or 0)
    return {'date':last.get('trade_date'),'close':last.get('close'),
            'change':last['close']*last['adj_factor']/denominator-1 if denominator and last.get('adj_factor') else None,
            'amount':(last.get('amount_thousand_cny') or 0)*1000,'volume':(last.get('vol_lot') or 0)*100,
            **daily_volume_ratio(rows,sessions or [],suspensions)}

def references(market,market_path,support_path):
    """Build only identity/hash/evidence references, after the shared input repair."""
    by_code={c:[] for c in market['quotes']}
    receipts=market.get('support',{}).get('requests',{})
    if receipts:
        with readonly(support_path) as c:
            for key,checksum in receipts.items():
                row=c.execute('SELECT endpoint,params,rows,checksum FROM responses WHERE request_id=?',(key,)).fetchone()
                if not row or row['checksum']!=checksum or digest(json.loads(row['rows']))!=checksum:raise ValueError('SUPPORT_REFERENCE_CHANGED')
                codes=set(json.loads(row['params']).get('ts_code','').split(','))
                for alias in market.get('support',{}).get('official_events',{}).get('aliases',[]):
                    if alias['previous_code'] in codes:codes.add(alias['code'])
                for code in codes & by_code.keys():by_code[code].append([key,checksum])
    result={}
    official=market.get('support',{}).get('official_events',{})
    for code,history in market['quotes'].items():
        rows=[{**r,'trade_date':day} for day,r in sorted(history.items())]
        meta=market['metadata'].get(code)
        files={e['source_file'] for e in official.get('halts',[])+official.get('aliases',[]) if e['code']==code}
        ref={'version':1,'code':code,'start':market['dates'][0],'end':market['actual_end'],
             'market_path':str(Path(market_path).resolve()),'support_path':str(Path(support_path).resolve()),
             'value_hash':fingerprint(rows,meta,market['dates']),
             'support_requests':by_code[code],'suspensions':market.get('suspensions',{}).get(code,{}),
             'official_sources':[s for s in official.get('sources',[]) if s['file'] in files]}
        result[code]={'ref':ref,'summary':summary(rows,sessions=market['dates'],suspensions=ref['suspensions']),'id':digest(ref)}
    return result

def load(ref):
    scope=_scope.get()
    if scope is None:
        with read_scope():return load(ref)
    code=ref['code']
    c=scope.connection(ref['market_path'])
    meta=c.execute('SELECT * FROM equity_master WHERE ts_code=?',(code,)).fetchone()
    meta=dict(meta) if meta else None
    rows=[{k:dict(r).get(k) for k in (*ROW_FIELDS,'pre_close','pct_chg')} for r in c.execute('''SELECT r.*,f.adj_factor
          FROM equity_daily_raw r LEFT JOIN equity_adj_factor f ON f.ts_code=r.ts_code AND f.trade_date=r.trade_date
          WHERE r.ts_code=? AND r.trade_date BETWEEN ? AND ? ORDER BY r.trade_date''',(code,ref['start'],ref['end']))]
    sessions=scope.calendar(ref)
    history={r['trade_date']:r for r in rows}
    if ref['support_requests']:
        for key,checksum in ref['support_requests']:
            endpoint,values=scope.receipt(ref['support_path'],key,checksum,code)
            for r in values:
                if endpoint=='stock_basic' and not meta:meta=r
                if not r.get('trade_date'):continue
                day=iso(r['trade_date'])
                if not ref['start']<=day<=ref['end']:continue
                if endpoint=='daily' and day not in history:
                    history[day]={k:r[k] for k in ('open','high','low','close')}
                    history[day].update(trade_date=day,vol_lot=r['vol'],amount_thousand_cny=r['amount'],adj_factor=None)
                if endpoint=='adj_factor' and day in history and not history[day].get('adj_factor'):
                    history[day]['adj_factor']=r['adj_factor']
    for source in ref.get('official_sources',[]):
        path=(Path(ref['support_path']).parent/source['file']).resolve()
        if not path.is_relative_to(Path(ref['support_path']).parent.resolve()) or not path.is_file():
            raise ValueError('停牌证据已变化，请本地重算')
        stamp=(path.stat().st_mtime_ns,path.stat().st_size)
        key=(str(path),stamp)
        if key not in scope.sources:scope.sources[key]=hashlib.sha256(path.read_bytes()).hexdigest()
        if scope.sources[key]!=source['sha256']:
            raise ValueError('停牌证据已变化，请本地重算')
    rows=[history[d] for d in sorted(history)]
    if fingerprint(rows,meta,sessions)!=ref['value_hash']:raise ValueError('行情已修订，需本地重算此集合；已保留原集合图')
    return {'rows':rows,'metadata':meta,'summary':summary(rows,sessions=sessions,suspensions=ref['suspensions']),'suspensions':ref['suspensions']}

def price_history(payload,mode='adjusted',*,carry_halts=False):
    """Shared validated adjustment for both charts and weekly signal projections."""
    if mode not in ('adjusted','raw'):raise ValueError('个股价格口径无效')
    raw=payload['rows']
    if not raw:return []
    anchor=raw[-1].get('adj_factor') if mode=='adjusted' else 1
    if not anchor or not math.isfinite(anchor) or anchor<=0:raise ValueError('缺少复权锚点')
    history=[]
    for r in raw:
        factor=r.get('adj_factor') if mode=='adjusted' else 1
        if not factor or not math.isfinite(factor) or factor<=0:raise ValueError('历史复权因子缺失')
        prices={k:r[k]*factor/anchor for k in ('open','high','low','close')}
        if any(not math.isfinite(v) or v<=0 for v in prices.values()):raise ValueError('个股行情无效')
        change=r.get('pct_chg')/100 if isinstance(r.get('pct_chg'),(int,float)) and math.isfinite(r['pct_chg']) else r['close']/r['pre_close']-1 if isinstance(r.get('pre_close'),(int,float)) and r['pre_close']>0 else None
        history.append({'trade_date':r['trade_date'],**prices,'volume':r['vol_lot']*100,'amount':r['amount_thousand_cny']*1000,'change_pct':change})
    if carry_halts and payload.get('suspensions'):
        observed={r['trade_date']:r for r in history};filled=[];previous=None
        for day in sorted(set(observed)|{d for d in payload['suspensions'] if history[0]['trade_date']<=d<=history[-1]['trade_date']}):
            row=observed.get(day)
            if row is None and previous is not None:
                row={'trade_date':day,**{k:previous['close'] for k in ('open','high','low','close')},
                     'volume':0,'amount':0,'is_suspended_valuation':True}
            if row is not None:filled.append(row);previous=row
        history=filled
    return history


def bars(ref,start,period='daily',mode='adjusted',sessions=None,*,series_reader=None):
    if _scope.get() is None:
        with read_scope():return bars(ref,start,period,mode,sessions,series_reader=series_reader)
    if period not in ('daily','weekly') or mode not in ('adjusted','raw'):raise ValueError('个股周期或价格口径无效')
    payload=load(ref)
    history=price_history(payload,mode,carry_halts=True)
    if not history:return [],payload,[]
    if series_reader is None:
        computed=calculator().chart_rows(history,period,calendar=_scope.get().calendar_snapshot(ref))
    else:
        from guanlan_domain.observer_math.indicators import apply_overlays
        if period=='weekly':
            weekly=series_reader.weekly_bars('stock',ref['code'],ref['start'],ref['end'],mode)
            computed=apply_overlays(weekly,{r['trade_date']:r.get('thirty_week_ma') for r in weekly})
        else:computed=apply_overlays(history,series_reader.daily_line('stock',ref['code'],payload['rows'],mode))
    from guanlan_domain.observer_math.chart_metrics import enrich
    computed=enrich(computed,period,_scope.get().calendar_snapshot(ref),payload['suspensions']) if _scope.get() else enrich(computed,period)
    selected=[r for r in computed if r['trade_date']>=start and (period=='weekly' or not r.get('is_suspended_valuation'))]
    events=[{'trade_date':d,'kind':'suspended','label':'全天停牌，无成交'} for d in sorted(payload['suspensions']) if d>=start]
    if period=='daily':
        present={r['trade_date'] for r in selected}
        for e in events:
            if e['trade_date'] not in present:selected.append(dict(trade_date=e['trade_date'],open=None,high=None,low=None,close=None,volume=0,amount=0,status='suspended'));present.add(e['trade_date'])
        listing=iso((payload.get('metadata') or {}).get('list_date')) or '9999-12-31'
        for day in sessions or []:
            if day>=max(start,listing) and day not in present:
                selected.append(dict(trade_date=day,open=None,high=None,low=None,close=None,volume=None,amount=None,status='missing'))
                events.append({'trade_date':day,'kind':'missing','label':'行情缺失，未作停牌估值'})
        selected.sort(key=lambda r:r['trade_date'])
    return selected,payload,events
