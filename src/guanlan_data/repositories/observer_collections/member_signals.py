"""Persisted, reference-bound equity signals. Browsing never computes history."""
import sqlite3
from pathlib import Path
from guanlan_data.repositories.observer_series.reader import Reader; from guanlan_data.repositories.observer_series.reader import unavailable
import guanlan_data.repositories.observer_series.sources as sources


def configured(ref=None):
    paths=sources.paths()
    if ref:paths={**paths,'stock':Path(ref['market_path']),'support':Path(ref['support_path'])}
    return paths


def signal(ref,as_of,*,mode='adjusted',reader=None):
    if reader is None:
        try:
            with Reader(configured(ref)) as view:return signal(ref,as_of,mode=mode,reader=view)
        except (ValueError,OSError,sqlite3.Error) as exc:return unavailable(str(exc),as_of=as_of)
    result=reader.members({ref['code']:ref},as_of)[ref['code']]
    if mode=='raw' and result.get('reason') not in ('行情或指标来源已修订，需本地重算','共享修复证据发生变化，需本地重算'):
        if reader.c.execute('SELECT 1 FROM series_bindings WHERE ref_id=?',(sources.digest(ref),)).fetchone():
            value=reader.signal('stock',ref['code'],ref['end'],'raw');value['expected_date']=as_of
            if value.get('quote_date') and value['quote_date']<as_of:value['status']='stale'
            return value
    return result


def _refs(raw):return {m['code']:raw['quotes'][m['code']]['ref'] for m in raw['group']['members']}


def members(raw,*,reader=None):
    refs=_refs(raw);as_of=raw['header']['actual_end']
    if reader is not None:
        support=sources.support_values(refs)
        revision=source_revision(raw,reader=reader,support=support)
        return {'target':{'kind':'group','id':raw['group']['id']},'data_revision':raw['publication_id'],
                'as_of':as_of,'source_revision':revision,'items':reader.members(refs,as_of,support=support)}
    try:
        with Reader(configured(next(iter(refs.values()),None))) as view:
            return members(raw,reader=view)
    except (OSError,sqlite3.Error) as exc:
        revision=None;items={code:unavailable(str(exc),as_of=as_of) for code in refs}
    except ValueError as exc:
        if '来源发生变化' in str(exc):raise
        revision=None;items={code:unavailable(str(exc),as_of=as_of) for code in refs}
    return {'target':{'kind':'group','id':raw['group']['id']},'data_revision':raw['publication_id'],
            'as_of':as_of,'source_revision':revision,'items':items}

def source_revision(raw,*,reader=None,support=None):
    refs=_refs(raw);first=next(iter(refs.values()),None)
    official=sorted({str((Path(r['support_path']).parent/s['file']).resolve()) for r in refs.values() for s in r.get('official_sources',())})
    if reader is not None:
        return sources.digest([reader.revision(),sources.support_values(refs) if support is None else support,[sources.stamp(p) for p in official]])
    try:
        with Reader(configured(first)) as view:return sources.digest([view.revision(),sources.support_values(refs),[sources.stamp(p) for p in official]])
    except (ValueError,OSError,sqlite3.Error) as exc:
        paths=configured(first)
        return sources.digest([str(exc),[sources.stamp(paths[k]) for k in ('stock','etf','support','results')]])
