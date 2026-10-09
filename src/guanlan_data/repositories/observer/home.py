"""Bounded, independently cached homepage projections. Original sources are read only.

No collector/build/update imports. A single stock date is read in a read transaction;
other modules use the existing reader contracts. File/WAL fences include signal and
classification sources, and changing sources get at most one retry per block.
"""
import copy
import math
import threading
import sqlite3
from datetime import date,timedelta
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_data.repositories.observer_movers.reader import normalize
from guanlan_data.repositories.observer_series.sources import paths; from guanlan_data.repositories.observer_series.sources import stamp

ROOT_IDS=tuple(f'I{i:02d}' for i in range(1,31))
BLOCKS=('stock','industry','etf','theme')
DEFINITIONS={
 'stock':'本地当日股票日线按证券代码去重。涨跌幅优先取pct_chg，缺失时取(close/pre_close−1)×100；无法判定另计。异动严格>7%或<−7%，不含±7%。',
 'industry':'固定30个一级行业，等权合成指数。只在同一观察日排行；当前站上30周线与近5周站上次数分别统计。落后日期、信号缺失不计趋势分母。',
 'etf':'同一行情日的代表ETF按自身涨跌幅排序，按代码去重。近5周信号属于同一ETF；组合质量单独显示。',
 'theme':'当前有效题材等权指数；同日期内排序。待发布、历史不足、旧日期分别显示。',
}

def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def market_counts(rows):
    unique={};duplicates=0;conflicts=set()
    for row in rows:
        code=row['ts_code']
        if code in unique:
            duplicates+=1
            if normalize(unique[code])[0]!=normalize(row)[0]:conflicts.add(code)
        else:unique[code]=row
    out={'source_rows':len(rows),'unique_codes':len(unique),'duplicates':duplicates,'conflicting_codes':len(conflicts),
         'valid_change':0,'up':0,'down':0,'flat':0,'large_up':0,'large_down':0,'unavailable_change':0,'invalid_ohlc':0,'fallback_change':0}
    for code,row in unique.items():
        value,source,valid=normalize(row)
        out['invalid_ohlc']+=not valid
        if code in conflicts:value=None
        if not finite(value):out['unavailable_change']+=1;continue
        out['valid_change']+=1;out['fallback_change']+=source=='daily.close/pre_close'
        out['up']+=value>0;out['down']+=value<0;out['flat']+=value==0
        out['large_up']+=value>7;out['large_down']+=value< -7
    return out

def weekly(signal,calendar):
    signal=signal or {};day=signal.get('as_of_date') or signal.get('quote_date')
    partial=None
    if day and calendar:
        start=date.fromisoformat(day)-timedelta(days=date.fromisoformat(day).weekday())
        weeks=calendar.weeks(start.isoformat(),(start+timedelta(days=6)).isoformat())
        if weeks and weeks[0]['known']:partial=weeks[0]['last_session']>day
    return {'strength':signal.get('strength_score'),'total':signal.get('total',5),
            'above_now':signal.get('above_now'),'consecutive':signal.get('consecutive_above_weeks'),
            'streak_is_lower_bound':bool(signal.get('streak_is_lower_bound')),
            'weekly_as_of':day,'is_partial_week':partial,'method_version':signal.get('method_version')}

def collection_projection(raw,module,calendar=None):
    rows=raw.get('items',[])
    if module=='industry':
        rows=[r for r in rows if r.get('group_id') in ROOT_IDS and not r.get('parent_id')]
    anchor=max((r.get('price_date') for r in rows if r.get('price_date')),default=None)
    by_id={r['group_id']:r for r in rows};items=[]
    ids=ROOT_IDS if module=='industry' else tuple(by_id)
    coverage={'expected':len(ids),'available':0,'stale':0,'missing':0,'insufficient':0,'trend_eligible':0,'above':0}
    for id in ids:
        r=by_id.get(id,{})
        day=r.get('price_date');value=r.get('group_return');w=weekly(r.get('weekly_strength'),calendar)
        status='missing' if not day or not finite(value) or r.get('pending') else 'stale' if day!=anchor else 'ready'
        signal_valid=w['above_now'] is not None and w['weekly_as_of']==anchor
        coverage['available']+=status=='ready';coverage['stale']+=status=='stale';coverage['missing']+=status=='missing'
        coverage['insufficient']+=status=='ready' and not signal_valid
        coverage['trend_eligible']+=status=='ready' and signal_valid
        coverage['above']+=status=='ready' and signal_valid and w['above_now'] is True
        items.append({'id':id,'name':r.get('group_name') or id,'date':day,'return':value if finite(value) else None,
                      'status':status,'signal_status':'ready' if w['above_now'] is not None and w['weekly_as_of']==day else 'insufficient',
                      'members':r.get('stats',{}).get('current_listed_members'),'publication_id':r.get('publication_id'),
                      'classification_revision':r.get('revision'),'quality':r.get('quality'),'pending':bool(r.get('pending')),
                      'target':{'module':'industry30' if module=='industry' else 'theme','kind':'group','id':id},**w})
    return {'as_of':anchor,'coverage':coverage,'items':items,'revision':raw.get('data_revision') or digest(items)}

def etf_projection(raw,calendar=None):
    rows=raw.get('items',[]);anchor=max((r.get('price_date') for r in rows if r.get('price_date')),default=None)
    unique={};older={};missing=0
    for r in rows:
        code=r.get('leader_etf_code');value=r.get('leader_return');day=r.get('price_date')
        if not code or not finite(value) or not day:missing+=1;continue
        item={'id':code,'name':r.get('leader_name') or code,'date':day,'return':value,'status':'ready' if day==anchor else 'stale',
                      'quality':r.get('quality_status'),'group_name':r.get('group_name'),
                      'target':{'module':'etf','kind':'group','id':r['group_id']},**weekly(r.get('weekly_strength'),calendar)}
        if day!=anchor:
            if code not in older or day>older[code]['date']:older[code]=item
            continue
        # A repeated representative has one deterministic destination.
        if code not in unique:unique[code]=item
    items=sorted(unique.values(),key=lambda r:(-r['return'],r['id']))
    old_items=sorted((v for code,v in older.items() if code not in unique),key=lambda r:(r['date'],r['return']),reverse=True)
    return {'as_of':anchor,'items':items[:4],'older_items':old_items,'coverage':{'available':len(items),'expected':len(rows),'stale':len(old_items),
                'missing':missing,'partial_groups':sum(r.get('quality_status')=='PARTIAL' for r in rows)},
            'revision':raw.get('data_revision') or digest(items)}

class HomeSummary:
    def __init__(self,data,*,source_paths=None,calendar_loader=None):
        self.data=data;self.paths=paths() if source_paths is None else source_paths
        self.production_sources=source_paths is None
        from guanlan_data.repositories.observer_collections.config import catalog_path
        from guanlan_data.repositories.industry_index.config import input_paths
        self.extra=[catalog_path(),input_paths()[0]] if source_paths is None else []
        if calendar_loader is None:
            from guanlan_data.repositories.observer_calendar import load
            calendar_loader=load
        self.calendar_loader=calendar_loader
        self.cache={};self.locks={k:threading.Lock() for k in BLOCKS}

    def fence(self,block=None):
        identity=[stamp(p) for p in (*self.paths.values(),*self.extra)]
        if block!='stock' and self.production_sources:
            from guanlan_data.repositories.observer_calendar import source_stamp
            identity.append(source_stamp(self.paths['stock']))
        return identity

    def query(self,params):
        if set(params)-{'view','block'} or params.get('view')!='summary' or params.get('block') not in BLOCKS:
            raise ValueError('首页摘要查询参数无效')
        block=params['block']
        with self.locks[block]:
            try:
                for attempt in range(2):
                    before=self.fence(block)
                    cached=self.cache.get(block)
                    if cached and cached[0]==before:return copy.deepcopy(cached[1])
                    calendar=self.calendar_loader() if block!='stock' else None
                    if block=='stock':result=self.stock()
                    else:
                        raw=self.data.query('industry30' if block=='industry' else block,{'view':'summary'})
                        result=etf_projection(raw,calendar) if block=='etf' else collection_projection(raw,block,calendar)
                    if before!=self.fence(block):continue
                    result={'schema_version':'guanlan_home_block_v1','block':block,
                            'status':'ready' if result.get('as_of') else 'empty','metric_definition':DEFINITIONS[block],
                            'source_identity':before,**result}
                    self.cache[block]=(before,copy.deepcopy(result));return result
                raise ValueError('来源正在更新，请重试')
            except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
                return {'schema_version':'guanlan_home_block_v1','block':block,'status':'error','as_of':None,
                        'revision':None,'items':[],'coverage':{},'metric_definition':DEFINITIONS[block],
                        'message':f'{dict(stock="股票",industry="行业",etf="ETF",theme="题材")[block]}数据读取失败',
                        'detail':str(exc)}

    def stock(self):
        with readonly(self.paths['stock']) as c:
            day=c.execute('SELECT MAX(trade_date) FROM equity_daily_raw').fetchone()[0]
            rows=[{k:(None if isinstance(v,float) and not math.isfinite(v) else v) for k,v in dict(r).items()}
                  for r in c.execute('SELECT ts_code,trade_date,open,high,low,close,pre_close,pct_chg FROM equity_daily_raw WHERE trade_date=? ORDER BY ts_code',(day,))] if day else []
            quarantined=0
            if day and c.execute("SELECT 1 FROM sqlite_master WHERE name='equity_daily_anomalies'").fetchone():
                quarantined=c.execute("SELECT COUNT(*) FROM equity_daily_anomalies WHERE trade_date=? AND resolution='quarantined_excluded'",(day,)).fetchone()[0]
        counts=market_counts(rows);counts['quarantined_records']=quarantined
        return {'as_of':day,'revision':digest([day,rows,quarantined]),'coverage':counts,'items':[]}
