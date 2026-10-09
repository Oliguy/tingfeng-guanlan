"""Durable local-only build, hosted in the existing update worker."""
from guanlan_data import sqlite as database
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import time
from guanlan_data.repositories.observer_calendar import load as load_calendar
from guanlan_domain.observer_math.indicators import week_key
from guanlan_domain.observer_math.signals import strength
from guanlan_data.repositories.industry_index.store import writer_lock
import guanlan_data.repositories.observer_collections.quotes as quotes
from guanlan_domain.observer_series import METHOD
import guanlan_data.repositories.observer_series.sources as sources; import guanlan_data.repositories.observer_series.store as store
from guanlan_domain.observer_series.calculation import build_weeks; from guanlan_domain.observer_series.calculation import project; from guanlan_domain.observer_series.calculation import effective_factors


def _normalized(raw):
    return [{**r,'volume':r['vol_lot']*100,'amount':r['amount_thousand_cny']*1000} for r in raw]


def _summary(rows,calendar,suspensions):
    raw=[{'trade_date':r['trade_date'],'vol_lot':(r.get('volume') or 0)/100} for r in rows]
    return quotes.daily_volume_ratio(raw,list(calendar.sessions(rows[0]['trade_date'],rows[-1]['trade_date'])),suspensions)


def _bind_refs(dest,asset,evidence,payloads):
    meta=dest.execute('SELECT * FROM series_meta WHERE asset=?',(asset,)).fetchone()
    if not meta:return
    for key,ref in evidence:
        payload=payloads.get(key)
        if not payload:
            dest.execute('DELETE FROM series_bindings WHERE ref_id=?',(key,));continue
        day=payload['summary']['date'];row=payload['rows'][-1] if payload['rows'] else None
        w=store.week(dest,asset,week_key(day)) if day else None
        if not w or not row:continue
        result=project(w,day,row['close'],row['adj_factor'])
        result.update({k:payload['summary'].get(k) for k in ('volume_ratio','volume_ratio_date','volume_ratio_reason')})
        result.update(expected_date=ref['end'],quote_date=day)
        if day<ref['end']:result['status']='stale'
        dest.execute('INSERT OR REPLACE INTO series_bindings VALUES(?,?,?,?,?,?,?)',
            (key,asset,ref['end'],ref['value_hash'],meta['input_hash'],store.canonical(result),store.canonical(ref)))


def build(*,job_id='local',progress=None,cancel=None,configured=None,kinds=('stock','etf'),codes=None,force=False):
    """Explicit invocation only. Sources are never written or fetched here."""
    p=configured or sources.paths();out=Path(p['results'])
    started=time.perf_counter();failed=[];done=unchanged=insufficient=0
    with writer_lock(out):
        store.initialize(out)
        with closing(database.connect(out,timeout=15)) as dest, quotes.read_scope():
            dest.row_factory=sqlite3.Row
            refs=sources.active_references(dest)
            by_code={}
            for key,ref in refs.items():by_code.setdefault(ref['code'],[]).append((key,ref))
            # One code at a time: do not retain millions of reference daily rows in memory.
            invalid_refs={}
            total=0
            inventories={}
            for kind in kinds:
                with closing(sources.open_read(p[kind])) as c:
                    field='ts_code' if kind=='stock' else 'etf_code';table='equity_daily_raw' if kind=='stock' else 'etf_daily'
                    old=store.checkpoint(dest,'source:'+kind)
                    marker=sources.source_marker(c,kind)
                    changed=sources.changes(c,kind,old.get('marker') if old else None,marker)
                    known=[r[0] for r in dest.execute('SELECT code FROM series_meta WHERE kind=?',(kind,))]
                    if known and old and changed is not None and (changed or old['stamp']==sources.stamp(p[kind])):
                        all_codes=sorted(set(known)|set(changed))
                    else:all_codes=[r[0] for r in c.execute('SELECT DISTINCT '+field+' FROM '+table)]
                    if codes is None:
                        for missing in sorted(set(known)-set(all_codes)):
                            asset=kind+':'+missing;reason='该证券本地行情已缺失，保留旧周线并停用强弱'
                            with dest:dest.execute('INSERT OR REPLACE INTO series_failures VALUES(?,?,?)',(asset,reason,job_id))
                            failed.append({'name':asset,'message':reason})
                    inventories[kind]=[code for code in all_codes if codes is None or code in codes]
                    total+=len(inventories[kind])
            job=store.checkpoint(dest,'job:'+job_id,{'id':job_id,'started_at':datetime.now(timezone.utc).isoformat(),'completed':[]})
            completed=set(job['completed'])
            states={}
            for kind in kinds:
                before=sources.stamp(p[kind]);old=store.checkpoint(dest,'source:'+kind)
                with closing(sources.open_read(p[kind])) as c:
                    marker=sources.source_marker(c,kind)
                    cal=load_calendar(p['stock'],connection=c if kind=='stock' else None)
                    dirty=sources.changes(c,kind,old.get('marker') if old else None,marker)
                    # No journal change plus an unexplained physical change is not proof of freshness.
                    if old and before!=old['stamp'] and dirty=={}:dirty=None
                    states[kind]={'stamp':before,'marker':marker,'path':str(p[kind]),'calendar_revision':cal.revision}
                    for code in inventories[kind]:
                        if cancel and cancel():
                            return {'status':'cancelled','done':done,'total':total,'failed':failed,'message':'本地指标已安全保存，可继续原任务'}
                        asset=kind+':'+code
                        meta=dest.execute('SELECT * FROM series_meta WHERE asset=?',(asset,)).fetchone()
                        evidence=by_code.get(code,[]) if kind=='stock' else []
                        evidence_hash=sources.digest(sorted({sources.digest({k:ref.get(k) for k in
                            ('support_requests','suspensions','official_sources')}) for key,ref in evidence}))
                        prior_evidence=store.checkpoint(dest,'evidence:'+asset)
                        saved=store.checkpoint(dest,'asset:'+asset,{})
                        own_fence=saved.get('source_fence') or {}
                        previously_failed=dest.execute('SELECT 1 FROM series_failures WHERE asset=?',(asset,)).fetchone()
                        unchanged_source=meta and not previously_failed and evidence_hash==prior_evidence and (
                            (dirty is not None and code not in dirty) or (own_fence.get('stamp')==before and own_fence.get('evidence')==evidence_hash))
                        unchanged_calendar=meta and meta['calendar_hash']==sources.calendar_used(cal,meta['start_date'],meta['end_date'])
                        reusable=bool(meta and meta['method']==METHOD and unchanged_source and unchanged_calendar and not force)
                        if reusable:
                            relevant_support=sources.support_values({key:ref for key,ref in evidence})
                            bound=True
                            for key,ref in evidence:
                                row=dest.execute('SELECT input_hash,value_hash,ref_json FROM series_bindings WHERE ref_id=?',(key,)).fetchone()
                                if not row or row['input_hash']!=meta['input_hash'] or row['value_hash']!=ref['value_hash'] or row['ref_json']!=store.canonical(ref):
                                    bound=False;break
                                if not sources.support_matches(ref,relevant_support):bound=False;break
                                try:sources.verify_official(ref)
                                except (ValueError,OSError):bound=False;break
                            if bound:
                                # A source-fenced existing binding needs no repeat daily/history read.
                                unchanged+=1;done+=1;continue
                        payloads={}
                        for key,ref in evidence:
                            try:payloads[key]=quotes.load(ref)
                            except (ValueError,OSError,sqlite3.Error) as e:invalid_refs[key]=str(e)
                        if meta and meta['method']==METHOD and unchanged_source and unchanged_calendar and not force:
                            with dest:_bind_refs(dest,asset,evidence,payloads)
                            unchanged+=1;done+=1;continue
                        try:
                            from_week=week_key(dirty[code]) if meta and dirty is not None and code in dirty and unchanged_calendar and evidence_hash==prior_evidence and not force else None
                            # Resume from the persisted preceding 29 weeks. Normal new-day work
                            # reads only this week, instead of the entire stock history.
                            seed=[]
                            if from_week:
                                records=dest.execute(store.JOIN+'WHERE asset=? AND week_start<? ORDER BY week_start DESC LIMIT 29',(asset,from_week)).fetchall()
                                seed=[store.decode_week(r) for r in reversed(records)]
                                if not seed:from_week=None
                            rows=sources.read_rows(c,kind,code,start=from_week)
                            if not rows:raise ValueError('该证券本地行情已缺失，保留旧周线并停用强弱')
                            suspensions={};merged={r['trade_date']:r for r in rows}
                            for key,ref in evidence:
                                payload=payloads.get(key)
                                if not payload:continue
                                suspensions.update(payload['suspensions'])
                                for r in _normalized(payload['rows']):
                                    if from_week and r['trade_date']<from_week:continue
                                    current=merged.get(r['trade_date'])
                                    if current is None:merged[r['trade_date']]=r
                                    elif not current.get('adj_factor') and r.get('adj_factor'):current['adj_factor']=r['adj_factor']
                            rows=[merged[d] for d in sorted(merged)]
                            weekly=build_weeks(rows,cal,kind=kind,suspensions=suspensions,seed=seed,range_start=from_week)
                            raw_weeks={}
                            for row in rows:raw_weeks.setdefault(week_key(row['trade_date']),[]).append(row)
                            for w in weekly:
                                w['quality']['input_hash']=sources.digest([raw_weeks.get(w['week_start'],[]),w['quality']])
                            older=[]
                            if from_week:
                                older=[(r['week_start'],json.loads(r['quality_json']).get('input_hash')) for r in dest.execute(
                                    'SELECT week_start,quality_json FROM series_weekly WHERE asset=? AND week_start<? ORDER BY week_start',(asset,from_week))]
                            input_hash=sources.digest([older+[(w['week_start'],w['quality']['input_hash']) for w in weekly],evidence_hash])
                            first=meta['start_date'] if meta and from_week else rows[0]['trade_date']
                            cal_hash=sources.calendar_used(cal,first,rows[-1]['trade_date'])
                            if meta and meta['input_hash']==input_hash and meta['calendar_hash']==cal_hash and meta['method']==METHOD and not force:
                                with dest:
                                    _bind_refs(dest,asset,evidence,payloads)
                                    dest.execute('DELETE FROM series_failures WHERE asset=?',(asset,))
                                unchanged+=1;done+=1;continue
                            if from_week:
                                # Six sessions for the close-of-day volume ratio, not a multi-year scan.
                                summary_rows=sources.read_rows(c,kind,code,start=cal.sessions(first,rows[-1]['trade_date'])[-6] if len(cal.sessions(first,rows[-1]['trade_date']))>=6 else first)
                            else:summary_rows=rows
                            summary=_summary(summary_rows,cal,suspensions)
                            if sources.stamp(p[kind])!=before:raise ValueError('计算期间行情源发生变化，本证券未发布')
                            store.save_asset(dest,asset,kind,code,rows,weekly,input_hash,cal_hash,summary,from_week=from_week,
                                             start_date=first,source_fence={'stamp':before,'evidence':evidence_hash,'calendar_revision':cal.revision},
                                             before_commit=lambda:_bind_refs(dest,asset,evidence,payloads))
                            with dest:
                                store.set_checkpoint(dest,'evidence:'+asset,evidence_hash)
                                completed.add(asset);job['completed']=sorted(completed);job['updated_at']=datetime.now(timezone.utc).isoformat()
                                store.set_checkpoint(dest,'job:'+job_id,job)
                            if weekly[-1]['signals']['adjusted']['above_now'] is None:insufficient+=1
                            done+=1
                        except (ValueError,OSError,TypeError,KeyError,sqlite3.Error) as e:
                            failed.append({'name':asset,'message':str(e)})
                            with dest:dest.execute('INSERT OR REPLACE INTO series_failures VALUES(?,?,?)',(asset,str(e),job_id))
                        if progress and (done%25==0 or failed):progress(f'本地周线/30周指标 {done}/{total} · 未知/不足 {insufficient} · 失败 {len(failed)}')
                    if sources.stamp(p[kind])!=before:raise ValueError('计算期间行情源发生变化，请继续本地重算')
            if dest.execute("SELECT 1 FROM sqlite_master WHERE name='publications'").fetchone():
                cal=load_calendar(p['stock'])
                for row in dest.execute("SELECT p.id,json_extract(p.group_json,'$.weekly_points') FROM current_publications h JOIN publications p ON p.id=h.publication_id").fetchall():
                    points=json.loads(row[1]);sig=strength(points,cal)
                    used=sources.calendar_used(cal,points[0]['trade_date'],points[-1]['trade_date']) if points else ''
                    with dest:dest.execute('INSERT OR REPLACE INTO series_group_signals VALUES(?,?,?)',(row[0],used,store.canonical(sig)))
            with dest:
                from guanlan_data.repositories.observer_calendar.repository import source_stamp as calendar_stamp
                store.set_checkpoint(dest,'calendar_snapshot',{'stamp':calendar_stamp(p['stock']),
                    'days':dict(cal.days),'revision':cal.revision,'sources':cal.sources,'official_years':cal.official_years})
                if codes is None:
                    for kind,state in states.items():store.set_checkpoint(dest,'source:'+kind,state)
                if codes is None and 'stock' in kinds:
                    store.set_checkpoint(dest,'support',{'stamp':sources.stamp(p['support']),'signature':sources.receipt_signature(p['support'])})
                job.update(status='partial' if failed else 'succeeded',finished_at=datetime.now(timezone.utc).isoformat())
                store.set_checkpoint(dest,'job:'+job_id,job)
                store.set_checkpoint(dest,'revision',sources.digest([tuple(r) for r in dest.execute(
                    'SELECT asset,input_hash,calendar_hash,method FROM series_meta ORDER BY asset')]))
            result={'status':'partial' if failed else 'succeeded','done':done,'total':total,'unchanged':unchanged,
                    'insufficient':insufficient,'invalid_references':invalid_refs,'failed':failed,
                    'elapsed_seconds':round(time.perf_counter()-started,3),
                    'as_of':dest.execute('SELECT MAX(end_date) FROM series_meta').fetchone()[0], 'job_id':job_id}
            if progress:progress(f'本地计算完成 {done}/{total}，失败 {len(failed)}，耗时 {result["elapsed_seconds"]}秒')
            return result
