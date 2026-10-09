"""ETF reader v1: one public API boundary plus a versioned readonly bars repository.

No subclassing, private hooks, collectors, file scans, or per-object detail queries.
The upstream v2 revision fences the supplemental read; mixed revisions never escape.
"""
from guanlan_data import sqlite as database
import copy
import importlib
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from guanlan_data.repositories.observer.config import settings; from guanlan_data.repositories.observer.config import installed_library
from guanlan_domain.observer.signals import etf_pair

SCHEMA='etf_observer_query_result_v2'
BAR_FIELDS=('trade_date','open','high','low','close','prev_close','volume','amount')


def public_session(data_root):
    sys.dont_write_bytecode=True
    from guanlan_data.repositories.guanlan_backend.etf_api import ObserverQuerySession; from guanlan_data.repositories.guanlan_backend.etf_api import validate_observer_params
    return ObserverQuerySession(data_root),validate_observer_params


class DailyBars:
    """Pinned SQLite read contract v1: schema versions 3/4, explicit named columns."""
    def __init__(self,data_root):
        self.path=Path(data_root)/'market_etf/industry_etf_observer.sqlite'

    def read(self,codes):
        unique=sorted({c for c in codes if c})
        if len(unique)>200:raise ValueError('ETF列表超出只读批量接口上限')
        result={}
        with closing(database.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
            row=conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            if not row or str(row[0]) not in ('3','4'):
                raise ValueError('ETF数据库版本与阅读器的只读接口不兼容')
            fields={r[1] for r in conn.execute('PRAGMA table_info(etf_daily)')}
            if not {'etf_code',*BAR_FIELDS}<=fields:
                raise ValueError('ETF行情字段不符合只读接口v1')
            for code in unique:
                rows=conn.execute('SELECT '+','.join(BAR_FIELDS)+' FROM etf_daily WHERE etf_code=? ORDER BY trade_date DESC LIMIT 400',(code,))
                result[code]=list(reversed([dict(r) for r in rows]))
        return result


class EtfReader:
    """Stable reader-facing query/close API. Injectable dependencies enable drift tests."""
    def __init__(self,data_root,*,session=None,validate=None,bars=None,calendar_loader=None,series_factory=None):
        if session is None:session,validate=public_session(data_root)
        self.session=session;self.validate=validate or (lambda p:p)
        self.bars=bars or DailyBars(data_root)
        # Injected bars retain the standalone compatibility calculator contract.
        # Desktop production always uses the shared persisted series service.
        self.persisted=bars is None or series_factory is not None
        if series_factory is None:
            from guanlan_data.repositories.observer_series.reader import Reader
            series_factory=Reader
        self.series_factory=series_factory
        from guanlan_data.repositories.observer_calendar import load as load_calendar
        self.calendar_loader=calendar_loader or load_calendar
        self.cache=None;self.lock=threading.RLock()

    def read(self,params):
        value=self.session.query(self.validate(params))
        if value.get('schema_version')!=SCHEMA or not value.get('data_revision'):
            raise ValueError('ETF公共查询返回了不兼容的数据合同')
        return value

    def query(self,params):
        with self.lock:
            if self.persisted:return self.persisted_query(params)
            params=self.validate(params)
            calendar=self.calendar_loader()
            if params.get('if_revision') and self.cache and self.cache[0][1]!=calendar.revision:
                params={k:v for k,v in params.items() if k!='if_revision'}
            for _ in range(2):
                result=self.read(params)
                if params['view']=='detail':
                    result=copy.deepcopy(result)
                    code=(result.get('leader') or {}).get('etf_code') or (result.get('etf') or {}).get('etf_code')
                    if code:
                        from guanlan_domain.observer_math import continuous_adjust_bars; from guanlan_domain.observer_math import chart_rows
                        from guanlan_domain.observer_math.signals import price_strength
                        history=self.bars.read([code])[code]
                        if params.get('price_mode','adjusted')!='raw':history,_=continuous_adjust_bars(history)
                        computed=chart_rows(history,params.get('period','daily'),calendar=calendar)
                        by_day={r['trade_date']:r for r in computed}
                        for bar in result.get('chart_bars',[]):
                            if bar['trade_date'] in by_day:
                                bar['thirty_week_ma']=by_day[bar['trade_date']]['thirty_week_ma']
                        result['weekly_strength']=price_strength(history,calendar)
                    else:
                        from guanlan_domain.observer_math.indicators import aggregate_weekly; from guanlan_domain.observer_math.indicators import week_key
                        from guanlan_domain.observer_math.signals import strength
                        points=result.get('chart_bars',[])
                        if params.get('period','daily')=='daily':
                            last={week_key(r['trade_date']):r for r in points}
                            points=[{**r,'thirty_week_ma':last[r['week_start']].get('thirty_week_ma')}
                                    for r in aggregate_weekly(points)]
                        result['weekly_strength']=strength(points,calendar)
                    check=self.read({'view':'summary','if_revision':result['data_revision']})
                    if not check.get('not_modified'):continue
                    result['calendar_revision']=calendar.revision
                    return result
                if params['view']!='summary' or result.get('not_modified'):return result
                revision=result['data_revision']
                if self.cache and self.cache[0]==(revision,calendar.revision):
                    signals=self.cache[1]
                else:
                    rows=result['items']+result['focus_items']
                    histories=self.bars.read([r.get('leader_etf_code') or r.get('etf_code') for r in rows])
                    signals={code:etf_pair(history,calendar) for code,history in histories.items()}
                    signals[None]=etf_pair([],calendar)
                    # Public revision includes both ETF and market source identity/version.
                    check=self.read({'view':'summary','if_revision':revision})
                    if not check.get('not_modified') or check['data_revision']!=revision:continue
                    self.cache=((revision,calendar.revision),signals)
                result=copy.deepcopy(result)
                for row in result['items']+result['focus_items']:
                    row['weekly_strength'].update(signals[row.get('leader_etf_code') or row.get('etf_code')])
                return result
            raise ValueError('ETF数据正在更新，未返回混合版本；请稍后重试')

    def persisted_query(self,params):
        import guanlan_data.repositories.observer_series.sources as sources
        from guanlan_domain.observer_math.indicators import apply_overlays; from guanlan_domain.observer_math.indicators import week_key
        from guanlan_domain.observer_math.chart_metrics import enrich
        from guanlan_data.repositories.industry_index.inputs import year_before
        from guanlan_data.repositories.observer_series.reader import unavailable
        params=self.validate(params)
        with self.series_factory() as view:
            identity=view.revision();calendar=view.calendar
            if params.get('if_revision') and (not self.cache or self.cache[0][1:]!=(calendar.revision,identity)):
                params={k:v for k,v in params.items() if k!='if_revision'}
            for _ in range(2):
                result=self.read(params)
                if result.get('not_modified') or params['view'] not in ('summary','detail'):return result
                revision=result['data_revision']
                if params['view']=='summary':
                    rows=result['items']+result['focus_items'];key=(revision,calendar.revision,identity)
                    if self.cache and self.cache[0]==key:signals=self.cache[1]
                    else:
                        codes={r.get('leader_etf_code') or r.get('etf_code') for r in rows}
                        signals={code:view.signal('etf',code,result.get('as_of')) if code else unavailable('暂无ETF行情') for code in codes}
                        check=self.read({'view':'summary','if_revision':revision})
                        if not check.get('not_modified') or check['data_revision']!=revision:continue
                        view.assert_unchanged();self.cache=(key,signals)
                    result=copy.deepcopy(result)
                    for row in result['items']+result['focus_items']:
                        row['weekly_strength']=copy.deepcopy(signals[row.get('leader_etf_code') or row.get('etf_code')])
                else:
                    result=copy.deepcopy(result)
                    code=(result.get('leader') or {}).get('etf_code') or (result.get('etf') or {}).get('etf_code')
                    bars=result.get('chart_bars',[]);mode=params.get('price_mode','adjusted')
                    if code and bars:
                        end=result.get('price_date') or bars[-1]['trade_date'];start=bars[0].get('period_start') or bars[0]['trade_date']
                        if params.get('period','daily')=='weekly':
                            weekly=view.weekly_bars('etf',code,year_before(start),end,mode)
                            calculated=apply_overlays(weekly,{r['trade_date']:r.get('thirty_week_ma') for r in weekly})
                            calculated=enrich(calculated,'weekly',calendar)
                            old={week_key(r['trade_date']):r for r in bars}
                            result['chart_bars']=[{**old.get(r['week_start'],{}),**r} for r in calculated if r['week_start']>=week_key(start)]
                        else:
                            raw=sources.read_rows(view.source_connection('etf'),'etf',code,start=start,end=end)
                            line=view.daily_line('etf',code,raw,mode)
                            for bar in bars:bar['thirty_week_ma']=line.get(bar['trade_date'])
                            history=list(reversed([dict(r) for r in view.source_connection('etf').execute('SELECT '+','.join(BAR_FIELDS)+' FROM etf_daily WHERE etf_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 600',(code,end))]))
                            for r in history:r['change_pct']=r['close']/r['prev_close']-1 if r.get('prev_close') and r.get('close') is not None else None
                            metrics={r['trade_date']:r for r in enrich(history,'daily',calendar)}
                            for bar in bars:
                                row=metrics.get(bar['trade_date'],{})
                                for key in ('change_pct','change_reason','volume_ratio','volume_ratio_reason'):bar[key]=row.get(key)
                        result['weekly_strength']=view.signal('etf',code,end,mode)
                    else:
                        result['weekly_strength']=unavailable('暂无ETF行情')
                        result['chart_bars']=enrich(bars,params.get('period','daily'),calendar)
                    check=self.read({'view':'summary','if_revision':revision})
                    if not check.get('not_modified'):continue
                    view.assert_unchanged()
                result['calendar_revision']=calendar.revision;result['series_revision']=identity
                return result
            raise ValueError('ETF数据正在更新，未返回混合版本；请稍后重试')

    def close(self):
        with self.lock:
            self.session.close();self.cache=None
