"""Read-only batch/as-of/chart projection. Missing materialization has a terminal state."""
from guanlan_data.config import source_path
from guanlan_data.layout import same_source_stamp
from contextlib import ExitStack,closing
from functools import lru_cache
import json
import hashlib
import sqlite3
from guanlan_data.repositories.observer_calendar import load as load_calendar
from guanlan_domain.observer_math.indicators import week_key
from guanlan_domain.observer_series import METHOD
import guanlan_data.repositories.observer_series.sources as sources; import guanlan_data.repositories.observer_series.store as store
from guanlan_domain.observer_series.calculation import project; from guanlan_domain.observer_series.calculation import display_week; from guanlan_domain.observer_series.calculation import effective_factors; from guanlan_domain.observer_series.calculation import finite


def unavailable(reason='指标尚未生成，请在更新面板重算本地指标',status='unavailable',as_of=None):
    return {'status':status,'reason':reason,'expected_date':as_of,'above_now':None,'strength_score':None,
            'consecutive_above_weeks':None,'two_weeks_above':False,'distance_30w':None,'method_version':METHOD}


@lru_cache(maxsize=8)
def validated_snapshot(serialized):
    cached=json.loads(serialized)
    content=hashlib.sha256(json.dumps([sorted(cached['days'].items()),cached['official_years']],separators=(',',':')).encode()).hexdigest()
    if content!=cached['revision']:return None
    return cached


@lru_cache(maxsize=8)
def cached_calendar(serialized):
    cached=validated_snapshot(serialized)
    if cached is None:return None
    from guanlan_domain.observer_calendar.core import Calendar
    return Calendar(cached['days'],revision=cached['revision'],sources=cached['sources'],official_years=cached['official_years'])


@lru_cache(maxsize=16)
def changed_source(path,kind,old_json,current_stamp):
    old=json.loads(old_json)
    with closing(sources.open_read(path)) as c:
        now=sources.source_marker(c,kind)
        dirty=sources.changes(c,kind,old['marker'],now)
        if now==old['marker'] and json.loads(current_stamp)!=old['stamp'] and not same_source_stamp(__import__('guanlan_data.config',fromlist=['current']).current().path('storage_root'),old['stamp'],json.loads(current_stamp)):dirty=None
    return dirty


class Reader:
    def __init__(self,configured=None):
        self.paths=configured or sources.paths();self.stack=ExitStack();self.connections={}
        self.before={k:sources.stamp(self.paths[k]) for k in ('stock','etf','support')}
        self._calendar=None;self._snapshot=None;self.dirty={};self.range_hashes={};self.support_versions=[];self.evidence_cache={}

    @property
    def calendar(self):
        if self._calendar is None:
            self._calendar=cached_calendar(self._snapshot) if self._snapshot else load_calendar(self.paths['stock'])
        return self._calendar

    def __enter__(self):
        try:return self._open()
        except BaseException:self.stack.close();raise

    def _open(self):
        self.c=self.stack.enter_context(store.read(self.paths['results']))
        self.binding_index=bool(self.c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='series_binding_read'").fetchone())
        schema=store.checkpoint(self.c,'schema')
        from guanlan_domain.observer_series import SCHEMA
        if schema!=SCHEMA:self.stack.close();raise ValueError('指标尚未生成，请重算本地指标')
        from guanlan_data.repositories.observer_calendar.repository import source_stamp as calendar_stamp
        cached=store.checkpoint(self.c,'calendar_snapshot')
        if cached and store.canonical(cached['stamp'])==store.canonical(calendar_stamp(self.paths['stock'])):
            serialized=store.canonical(cached)
            if validated_snapshot(serialized) is not None:self._snapshot=serialized
        self.calendar_revision=cached['revision'] if self._snapshot else self.calendar.revision
        self.groups_current=bool(cached and cached['revision']==self.calendar_revision)
        self.calendar_current={};self.failures={r[0] for r in self.c.execute('SELECT asset FROM series_failures')}
        for kind in ('stock','etf'):
            old=store.checkpoint(self.c,'source:'+kind)
            self.calendar_current[kind]=bool(old and old.get('calendar_revision')==self.calendar_revision)
            self.dirty[kind]=({} if old and old['stamp']==self.before[kind] else
                              changed_source(str(self.paths[kind]),kind,store.canonical(old),store.canonical(self.before[kind])) if old else None)
        support=store.checkpoint(self.c,'support')
        self.support_changed=bool(support and support['stamp']!=self.before['support'])
        return self

    def __exit__(self,*args):
        try:
            if args[0] is None:self.assert_unchanged()
        finally:self.stack.close()

    def source_connection(self,kind):
        if kind not in self.connections:self.connections[kind]=self.stack.enter_context(closing(sources.open_read(self.paths[kind])))
        return self.connections[kind]

    def revision(self):return sources.digest([{k:v for k,v in self.before.items() if k!='support'},self.calendar_revision,store.checkpoint(self.c,'revision'),sorted(self.failures)])

    def assert_unchanged(self):
        if any(sources.stamp(self.paths[k])!=v for k,v in self.before.items() if k!='support'):
            raise ValueError('读取期间行情来源发生变化，请重试')
        if any(sources.support_values(refs)!=values for refs,values in self.support_versions):
            raise ValueError('读取期间修复来源发生变化，请重试')

    def evidence(self,meta):
        asset=meta['asset']
        if asset not in self.evidence_cache:
            refs={r[0]:json.loads(r[1]) for r in self.c.execute('SELECT ref_id,ref_json FROM series_bindings WHERE asset=? AND input_hash=?',(asset,meta['input_hash']))}
            values=sources.support_values(refs);self.support_versions.append((refs,values))
            try:
                valid=all(sources.support_matches(ref,values) for ref in refs.values())
                for ref in refs.values():sources.verify_official(ref)
            except (ValueError,OSError):valid=False
            self.evidence_cache[asset]=valid
        return self.evidence_cache[asset]

    def valid(self,meta,as_of,*,evidence=True):
        if meta['method']!=METHOD:return False
        if meta['asset'] in self.failures:return False
        if evidence and meta['kind']=='stock' and not self.evidence(meta):return False
        dirty=self.dirty[meta['kind']]
        invalid_source=dirty is None or (meta['code'] in dirty and dirty[meta['code']]<=as_of)
        own={}
        if invalid_source:
            own=store.checkpoint(self.c,'asset:'+meta['asset'],{}).get('source_fence') or {}
            if own.get('stamp')!=self.before[meta['kind']]:return False
        if self.calendar_current[meta['kind']] or own.get('calendar_revision')==self.calendar_revision:return True
        key=(meta['start_date'],meta['end_date'])
        if key not in self.range_hashes:self.range_hashes[key]=sources.calendar_used(self.calendar,*key)
        return self.range_hashes[key]==meta['calendar_hash']

    def members(self,refs,as_of,*,support=None):
        ids={code:sources.digest(ref) for code,ref in refs.items()};result={}
        support=sources.support_values(refs) if support is None else support;self.support_versions.append((refs,support))
        rows={}
        for offset in range(0,len(ids),400):
            keys=list(ids.values())[offset:offset+400]
            values=self.c.execute('''SELECT b.ref_id,b.asset,b.as_of,b.value_hash,b.input_hash,b.signal_json,
              m.kind,m.code,m.start_date,m.end_date,m.method,m.calendar_hash,m.anchor
              FROM series_bindings b '''+('INDEXED BY series_binding_read ' if self.binding_index else '')+'''JOIN series_meta m ON m.asset=b.asset AND m.input_hash=b.input_hash
              WHERE b.ref_id IN ('''+','.join('?' for _ in keys)+')',keys)
            rows.update({r['ref_id']:r for r in values})
        for code,ref in refs.items():
            row=rows.get(ids[code])
            if row is None:result[code]=unavailable(as_of=as_of);continue
            meta=dict(row)
            if row['value_hash']!=ref['value_hash'] or row['as_of']!=ref['end'] or not self.valid(meta,ref['end'],evidence=False):
                result[code]=unavailable('行情或指标来源已修订，需本地重算','unavailable',as_of);continue
            if not sources.support_matches(ref,support):
                result[code]=unavailable('共享修复证据发生变化，需本地重算','unavailable',as_of);continue
            try:sources.verify_official(ref)
            except (ValueError,OSError) as exc:
                result[code]=unavailable(str(exc),as_of=as_of);continue
            sig=json.loads(row['signal_json']);sig['expected_date']=as_of
            anchor=row['anchor']
            if sig.get('quote_date')!=row['end_date']:
                factor=self.source_connection('stock').execute('SELECT adj_factor FROM equity_adj_factor WHERE ts_code=? AND trade_date=?',(code,sig.get('quote_date'))).fetchone()
                anchor=factor[0] if factor else None
            if sig.get('thirty_week_ma') is not None and finite(anchor) and anchor>0:sig['thirty_week_ma']/=anchor
            if sig.get('quote_date') and sig['quote_date']<as_of:sig['status']='stale'
            result[code]=sig
        self.assert_unchanged()
        return result

    def signal(self,kind,code,as_of=None,policy='adjusted'):
        asset=kind+':'+code;meta=self.c.execute('SELECT * FROM series_meta WHERE asset=?',(asset,)).fetchone()
        if not meta:return unavailable(as_of=as_of)
        day=as_of or meta['end_date']
        if not self.valid(meta,day):return unavailable('行情或日历已修订，需本地重算',as_of=day)
        if day>=meta['end_date']:
            row=self.c.execute('SELECT signal_json FROM series_latest_signal WHERE asset=? AND policy=?',(asset,policy)).fetchone()
            value=json.loads(row[0]);value['expected_date']=day
            if policy=='adjusted' and value.get('thirty_week_ma') is not None and finite(meta['anchor']) and meta['anchor']>0:value['thirty_week_ma']/=meta['anchor']
            if meta['end_date']<day:value['status']='stale'
            return value
        c=self.source_connection(kind)
        field,table=('ts_code','equity_daily_raw') if kind=='stock' else ('etf_code','etf_daily')
        actual=c.execute('SELECT MAX(trade_date) FROM '+table+' WHERE '+field+'=? AND trade_date<=?',(code,day)).fetchone()[0]
        if not actual:return unavailable('该日之前无本地行情',as_of=day)
        row=sources.read_rows(c,kind,code,start=actual,end=actual)[0]
        w=store.week(self.c,asset,week_key(actual))
        if not w:return unavailable(as_of=day)
        factor=row.get('adj_factor') if kind=='stock' else w['close_factor']
        if kind=='etf' and w['mixed_factor']:
            days=sources.read_rows(c,kind,code,start=w['week_start'],end=actual)
            factor=effective_factors(days,kind,initial_factor=w['open_factor'])[actual]
        value=project(w,actual,row['close'],factor,policy=policy);value['expected_date']=day
        if policy=='adjusted' and value.get('thirty_week_ma') is not None and finite(factor) and factor>0:value['thirty_week_ma']/=factor
        if actual<day:value['status']='stale'
        return value

    def weekly_bars(self,kind,code,start,end,policy='adjusted'):
        asset=kind+':'+code;meta=self.c.execute('SELECT * FROM series_meta WHERE asset=?',(asset,)).fetchone()
        if not meta or not self.valid(meta,end):raise ValueError('周线尚未生成或已修订，请重算本地指标')
        c=self.source_connection(kind)
        final=sources.read_rows(c,kind,code,start=end,end=end)
        if not final:
            field,table=('ts_code','equity_daily_raw') if kind=='stock' else ('etf_code','etf_daily')
            actual=c.execute('SELECT MAX(trade_date) FROM '+table+' WHERE '+field+'=? AND trade_date<=?',(code,end)).fetchone()[0]
            final=sources.read_rows(c,kind,code,start=actual,end=actual) if actual else []
        if not final:return []
        actual=final[0]['trade_date'];last_week=store.week(self.c,asset,week_key(actual))
        anchor=final[0].get('adj_factor') if kind=='stock' else last_week['close_factor']
        if kind=='etf' and last_week['trade_date']>actual:
            last_days=sources.read_rows(c,kind,code,start=last_week['week_start'],end=actual)
            anchor=effective_factors(last_days,kind,initial_factor=last_week['open_factor'])[actual]
        result=[]
        for w in store.weeks(self.c,asset,week_key(start),week_key(actual)):
            days=None
            # Historical intra-week cutoff and split weeks aggregate only that one week.
            partial=w['trade_date']>actual
            if partial or (policy=='adjusted' and w['mixed_factor']):
                raw=sources.read_rows(c,kind,code,start=w['week_start'],end=min(w['trade_date'],actual))
                factors=effective_factors(raw,kind,initial_factor=w['open_factor'])
                days=[{**r,'effective_factor':factors[r['trade_date']]} for r in raw]
                if partial:
                    last=days[-1];w={**w,'trade_date':actual,'close':last['close'],'close_factor':last['effective_factor'],
                        'open':days[0]['open'],'high':max(d['high'] for d in days),'low':min(d['low'] for d in days),
                        'volume':sum(d.get('volume') or 0 for d in days),'amount':sum(d.get('amount') or 0 for d in days),
                        'quality':{**w['quality'],**{k:[d for d in w['quality'][k] if d<=actual] for k in ('missing','invalid','factor_missing')}},
                        'signals':{p:project(w,actual,last['close'],last['effective_factor'],policy=p) for p in ('raw','adjusted')}}
                    if kind=='etf':anchor=last['effective_factor']
            result.append(display_week(w,anchor,policy=policy,days=days))
        self.assert_unchanged();return result

    def daily_line(self,kind,code,rows,policy='adjusted'):
        if not rows:return {}
        asset=kind+':'+code;meta=self.c.execute('SELECT * FROM series_meta WHERE asset=?',(asset,)).fetchone()
        if not meta or not self.valid(meta,rows[-1]['trade_date']):return {}
        weeks={w['week_start']:w for w in store.weeks(self.c,asset,week_key(rows[0]['trade_date']),week_key(rows[-1]['trade_date']))}
        if kind=='stock':factors={r['trade_date']:r.get('adj_factor') for r in rows}
        else:
            first=weeks.get(week_key(rows[0]['trade_date']))
            # Include the start of the first displayed week before forward ETF factors.
            source=sources.read_rows(self.source_connection(kind),kind,code,start=first['week_start'],end=rows[-1]['trade_date']) if first else rows
            factors=effective_factors(source,kind,initial_factor=first['open_factor'] if first else 1)
        anchor=factors.get(rows[-1]['trade_date']);output={}
        for row in rows:
            w=weeks.get(week_key(row['trade_date']))
            if not w:continue
            value=project(w,row['trade_date'],row['close'],factors.get(row['trade_date']),policy=policy)
            ma=value['thirty_week_ma']
            output[row['trade_date']]=ma/anchor if policy=='adjusted' and ma is not None and finite(anchor) and anchor>0 else ma
        self.assert_unchanged();return output

    def group_signal(self,publication,points):
        row=self.c.execute('SELECT * FROM series_group_signals WHERE publication_id=?',(publication,)).fetchone()
        if not row or not points:return None
        if not self.groups_current and row['calendar_hash']!=sources.calendar_used(self.calendar,points[0]['trade_date'],points[-1]['trade_date']):return None
        return json.loads(row['signal_json'])

    def group_signals(self,publications):
        if not self.groups_current:return None
        keys=list(publications)
        if not keys:return {}
        return {r[0]:json.loads(r[1]) for r in self.c.execute('SELECT publication_id,signal_json FROM series_group_signals WHERE publication_id IN ('+','.join('?' for _ in keys)+')',keys)}
