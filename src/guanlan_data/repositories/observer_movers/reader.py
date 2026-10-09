"""One stock source, daily anomaly DTO and bounded all-market stock charts."""
import copy, math, re, threading
from collections import OrderedDict
from datetime import date
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import year_before
from guanlan_data.repositories.observer_movers.catalog import Catalog; from guanlan_data.repositories.observer_movers.catalog import stamp
from guanlan_data.repositories.observer_movers.limits import read_day; from guanlan_data.repositories.observer_movers.limits import finite

FIELDS=('ts_code','trade_date','open','high','low','close','pre_close','pct_chg','vol_lot','amount_thousand_cny')

def source_values(row):
    return {k:row.get(k) if not isinstance(row.get(k),float) or math.isfinite(row[k]) else None for k in FIELDS}

def normalize(row):
    value=row['pct_chg'];source='daily.pct_chg'
    if not finite(value):
        value=(row['close']/row['pre_close']-1)*100 if finite(row['close']) and finite(row['pre_close']) and row['pre_close']>0 else None
        source='daily.close/pre_close' if value is not None else 'unavailable'
    prices=[row[k] for k in ('open','high','low','close')]
    valid=all(finite(v) and v>0 for v in prices) and row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']
    return value,source,valid

class Reader:
    def __init__(self,market=None,business=None,theme=None,support=None,kph=None,catalog=None):
        from guanlan_data.repositories.industry_index.config import input_paths
        from guanlan_data.repositories.observer_collections.config import catalog_path; from guanlan_data.repositories.observer_collections.config import support_path
        if market is None or (business is None and catalog is None):
            b,m=input_paths();market=market or m;business=business or b
        self.market=Path(market);self.support=Path(support or support_path());self.business=business
        self.kph=Path(kph) if kph else self.market.parent.parent/'market_events/kph_limit_up.sqlite'
        self.catalog=catalog or Catalog(business,theme or catalog_path()).read
        self.scoped_catalog=catalog is None
        self.cache=OrderedDict();self.lock=threading.Lock()
        self.leader_cache=OrderedDict();self.leader_lock=threading.Lock()

    def query(self,params):
        view=params.get('view','day')
        allowed={'day':{'view','date'},'dates':{'view','before','limit'},'stock':{'view','date','event_date','target','period','price_mode','event_revision','classification_revision'},
                 'leader':{'view','date','market_revision','classification_revision'}}
        if view not in allowed or set(params)-allowed[view]:raise ValueError('异动查询参数无效')
        if view=='dates':return self.dates(params)
        if view=='stock':return self.stock(params)
        if view=='leader':return self.leader(params)
        return self.day(params.get('date'))

    def leader(self,params):
        from guanlan_data.repositories.observer_movers.leader_reader import read_leader
        from guanlan_domain.observer_movers.leader_score import MODEL_VERSION
        from guanlan_domain.observer_movers.limit_rules import RULE_VERSION
        from guanlan_data.repositories.observer_calendar import source_stamp as calendar_stamp
        selected=params.get('date')
        if selected:self.date(selected)
        key=(MODEL_VERSION,RULE_VERSION,selected,stamp(self.market),stamp(self.support),stamp(self.kph),
             calendar_stamp(self.market),self.catalog()['revision'])
        with self.leader_lock:
            value=self.leader_cache.get(key)
            if value is None:
                value=read_leader(self,selected,expected_market=params.get('market_revision'),
                                  expected_classification=params.get('classification_revision'))
                self.leader_cache[key]=value
                while len(self.leader_cache)>4:self.leader_cache.popitem(last=False)
        if params.get('market_revision') and params['market_revision']!=value['market_revision']:
            raise ValueError('当日行情已修订，请刷新异动列表')
        if params.get('classification_revision') and params['classification_revision']!=value['classification_revision']:
            raise ValueError('分类目录已修订，请刷新异动列表')
        return copy.deepcopy(value)

    @staticmethod
    def date(value):
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError('请选择有效采集日期')
        return value

    def dates(self,params):
        before=params.get('before');limit=params.get('limit',60)
        if before:self.date(before)
        if type(limit)is not int or not 1<=limit<=120:raise ValueError('日期页大小须为1–120')
        with readonly(self.market) as c:
            latest=c.execute('SELECT MAX(trade_date) FROM equity_daily_raw').fetchone()[0]
            rows=[r[0] for r in c.execute('SELECT DISTINCT trade_date FROM equity_daily_raw '+('WHERE trade_date<? ' if before else '')+'ORDER BY trade_date DESC LIMIT ?',((before,limit+1) if before else (limit+1,)))]
            return {'schema_version':'movers_dates_v1','latest_collected_trade_date':latest,'dates':rows[:limit],'next_cursor':rows[limit-1] if len(rows)>limit else None,'source_revision':digest([latest,stamp(self.market)])}

    def day(self,selected=None):
        if selected:self.date(selected)
        if selected:
            source_stamps=(stamp(self.market),stamp(self.support),stamp(self.kph))
            with self.lock:
                prior=next(((key,value) for key,value in reversed(self.cache.items())
                            if key[0]==selected and key[7:10]==source_stamps),None)
            if prior:
                key,value=prior
                codes=[row['code'] for row in value['rows']]
                catalog=self.catalog(codes) if self.scoped_catalog else self.catalog()
                if (catalog['revision']==value['classification_revision'] and
                    source_stamps==(stamp(self.market),stamp(self.support),stamp(self.kph))):
                    with self.lock:
                        if key in self.cache:self.cache.move_to_end(key)
                    return copy.deepcopy(value)
        with readonly(self.market) as c:
            latest=c.execute('SELECT MAX(trade_date) FROM equity_daily_raw').fetchone()[0]
            day=selected or latest
            if not day:raise ValueError('本地尚无股票日线')
            previous=c.execute('SELECT MAX(trade_date) FROM equity_daily_raw WHERE trade_date<?',(day,)).fetchone()[0]
            next_day=c.execute('SELECT MIN(trade_date) FROM equity_daily_raw WHERE trade_date>?',(day,)).fetchone()[0]
            raw=[dict(r) for r in c.execute('SELECT r.*,m.name FROM equity_daily_raw r LEFT JOIN equity_master m USING(ts_code) WHERE r.trade_date=? ORDER BY r.ts_code',(day,))]
            quarantined=0
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='equity_daily_anomalies'").fetchone():
                quarantined=c.execute("SELECT COUNT(*) FROM equity_daily_anomalies WHERE trade_date=? AND resolution='quarantined_excluded'",(day,)).fetchone()[0]
        if not raw:raise ValueError('该日期尚无已采集日线')
        codes=[r['ts_code'] for r in raw if (value:=normalize(r)[0]) is not None and abs(value)>7]
        from guanlan_data.repositories.observer_collections.securities import identities
        names=identities(codes,self.market,self.business,self.support)
        for r in raw:
            if r['ts_code'] in names:r['name']=names[r['ts_code']]['name']
        catalog=self.catalog(codes) if self.scoped_catalog else self.catalog()
        market_revision=digest([{**source_values(r),'name':r.get('name')} for r in raw])
        key=(day,latest,previous,next_day,market_revision,quarantined,catalog['revision'],stamp(self.market),stamp(self.support),stamp(self.kph))
        with self.lock:
            cached=self.cache.get(key)
            if cached:self.cache.move_to_end(key);return copy.deepcopy(cached)
        rows=[];invalid=0;invalid_pct=0
        for r in raw:
            change,source,valid=normalize(r)
            invalid+=not valid;invalid_pct+=change is None
            if change is None or not (change>7 or change < -7):continue
            rows.append({'code':r['ts_code'],'name':r.get('name') or '', 'pct_chg':change,'pct_source':source,'close':r['close'] if finite(r['close']) else None,
                         'amount':r['amount_thousand_cny']*1000 if finite(r['amount_thousand_cny']) else None,'quality':'available' if valid else 'invalid_ohlc',
                         'event_revision':digest(source_values(r)),'boards':[],'subboards':[],'themes':[]})
        index={r['code']:r for r in rows};definitions={'industry':[],'subindustry':[],'theme':[]};fallback={}
        for group in catalog['groups']:
            kind=group['kind'];axis='subindustry' if kind=='subindustry' else 'industry'
            entry={k:group.get(k) for k in ('id','name','parent_id','revision','path')};codes=[]
            for member in group.get('members',[]):
                if member.get('name') and not fallback.get(member['code']):fallback[member['code']]=member['name']
                if member['code'] in index:
                    codes.append(member['code']);index[member['code']]['subboards' if axis=='subindustry' else 'boards'].append(entry)
            if codes:definitions[axis].append({**entry,'codes':sorted(set(codes))})
        for theme in catalog['themes']:
            codes=[]
            for member in theme['members']:
                if member.get('name') and not fallback.get(member['code']):fallback[member['code']]=member['name']
                if member['code'] not in index:continue
                codes.append(member['code'])
                tags=[t for t in theme['tags'] if any(p[:len(t['path'])]==t['path'] for p in member['paths'])]
                index[member['code']]['themes'].append({'id':theme['id'],'name':theme['name'],'revision':theme['revision'],'paths':member['paths'],'tags':tags})
            if codes:definitions['theme'].append({'id':theme['id'],'name':theme['name'],'revision':theme['revision'],'codes':sorted(set(codes))})
        from .recent_limits import read as read_recent
        recent,coverage=read_recent(self,day,list(index))
        for row in rows:
            row['name']=row['name'] or fallback.get(row['code']) or '名称待补齐'
            row.update(recent.get(row['code'],{'limit':{'status':'unknown','label':'证据不足','sources':[]},'limit_sequence':{'label':'','days':[]}}))
        for axis,field,label in (('industry','boards','未分类'),('subindustry','subboards','未划入子板块'),('theme','themes','未录入题材')):
            missing=[r['code'] for r in rows if not r[field]]
            if missing:definitions[axis].append({'id':'unassigned:'+axis,'name':label,'codes':missing,'revision':catalog['revision']})
        for groups in definitions.values():
            for g in groups:
                rr=[index[code] for code in g['codes']];g['stats']={'total':len(rr),'up':sum(r['pct_chg']>7 for r in rr),'down':sum(r['pct_chg'] < -7 for r in rr),'limit_up':sum(r['limit']['status']=='confirmed_up' for r in rr)}
        result={'schema_version':'movers_display_v1','date':day,'latest_collected_trade_date':latest,'previous_date':previous,'next_date':next_day,
                'data_revision':digest([day,market_revision,rows,catalog['revision'],coverage,quarantined]),'market_revision':market_revision,'classification_revision':catalog['revision'],
                'classification_basis':'当前目录关系，用于历史日分组；不是该日历史分类','rows':rows,'groups':definitions,
                'theme_catalog':[{k:t[k] for k in ('id','name','revision','tags')} for t in catalog['themes']],
                'stats':{'total':len(rows),'up':sum(r['pct_chg']>7 for r in rows),'down':sum(r['pct_chg'] < -7 for r in rows),'limit_up':sum(r['limit']['status']=='confirmed_up' for r in rows),'limit_unknown':sum(r['limit']['status'] in ('unknown','conflict') for r in rows)},
                'quality':{'source_rows':len(raw),'invalid_ohlc':invalid,'unavailable_change':invalid_pct,'quarantined_records':quarantined,'limit_coverage':coverage}}
        with self.lock:
            self.cache[key]=result
            while len(self.cache)>24:self.cache.popitem(last=False)
        return copy.deepcopy(result)

    def stock(self,params):
        from guanlan_data.repositories.observer_collections.quotes import price_history
        from guanlan_domain.observer_math.indicators import apply_overlays
        from guanlan_data.repositories.observer_series.reader import Reader as SeriesReader
        import guanlan_data.repositories.observer_series.sources as sources
        day=self.date(params.get('date'));target=params.get('target',{})
        event_day=self.date(params.get('event_date',day))
        if event_day>day:raise ValueError('异动日不能晚于图表截至日')
        if params.get('classification_revision') and params['classification_revision']!=self.day(day)['classification_revision']:raise ValueError('分类目录已修订，请返回异动刷新列表')
        if not isinstance(target,dict) or set(target)!={'kind','id'} or target['kind']!='stock' or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',str(target['id'])):raise ValueError('股票目标无效')
        period=params.get('period','daily');mode=params.get('price_mode','adjusted')
        if period not in ('daily','weekly') or mode not in ('adjusted','raw'):raise ValueError('行情周期或口径无效')
        start='1900-01-01';warm=start;code=target['id']
        with readonly(self.market) as c:
            event=c.execute('SELECT * FROM equity_daily_raw WHERE ts_code=? AND trade_date=?',(code,event_day)).fetchone()
            if not event:raise ValueError('当日股票行情不存在')
            event=dict(event);value,source,valid=normalize(event)
            revision=digest(source_values(event))
            if params.get('event_revision') and params['event_revision']!=revision:raise ValueError('当日行情已修订，请返回异动刷新列表')
            if value is None or not (value>7 or value < -7):raise ValueError('该股票不再满足当日异动条件，请刷新列表')
            if not valid:raise ValueError('股票OHLC无效，已保留异动记录；暂不能绘图')
            raw=[dict(r) for r in c.execute('SELECT r.*,f.adj_factor FROM equity_daily_raw r LEFT JOIN equity_adj_factor f USING(ts_code,trade_date) WHERE r.ts_code=? AND r.trade_date BETWEEN ? AND ? ORDER BY r.trade_date',(code,warm,day))]
            if event_day!=day:
                from guanlan_data.repositories.observer_calendar import load as load_event_calendar
                event_calendar=load_event_calendar(self.market,connection=c)
                five=list(event_calendar.sessions('1900-01-01',day))[-5:]
                if event_day not in five:raise ValueError('异动日须在所选日近5个开市日内')
            meta=c.execute('SELECT * FROM equity_master WHERE ts_code=?',(code,)).fetchone();meta=dict(meta) if meta else {}
            from guanlan_data.repositories.observer_calendar import load as load_calendar
            calendar=load_calendar(self.market,connection=c)
            sessions=list(calendar.sessions(start,day))
        from guanlan_data.repositories.observer_chart_history import stock_history
        chart=stock_history(self.market,code,day,period,mode)
        points=chart['bars'];events=chart['events'];start=chart['start'];weekly_signal=chart['signal'];series_revision=chart['revision']
        from guanlan_data.repositories.observer_collections.securities import identities
        identity=identities([code],self.market,self.business,self.support).get(code,{})
        meta={**meta,**identity}
        evidence,_=read_day(self.support,self.kph,event_day,[event])
        latest_quote=next((r for r in reversed(raw) if r['trade_date']==day),None)
        latest_change=normalize(latest_quote)[0] if latest_quote else None
        return {'module':'movers','target':target,'data_revision':digest([raw,meta,revision,series_revision]),'period':period,'price_mode':mode,
                'parent':{'kind':'movers','id':event_day,'name':event_day+' 异动'},'price_date':day,'chart_bars':points,'series':[],
                'units':{'price':'元/股','volume':'股','amount':'元'},'weekly_strength':weekly_signal,
                'stock':{'code':code,'name':meta.get('name') or code,'status':'market_source','metadata':meta,'events':events,'relations':[],
                         'quote':{'date':day,'close':latest_quote['close'] if latest_quote else None,'change':latest_change/100 if latest_change is not None else None,'amount':latest_quote['amount_thousand_cny']*1000 if latest_quote and finite(latest_quote['amount_thousand_cny']) else None,'volume':latest_quote['vol_lot']*100 if latest_quote and finite(latest_quote['vol_lot']) else None}},
                'event':{'date':event_day,'pct_chg':value,'pct_source':source,'revision':revision,'limit':evidence[code]},'header':{'start_date':start,'actual_end':day}}
