"""Explicit, read-only adapters. No discovery, collectors or external requests."""
from guanlan_data.config import source_path
from guanlan_data.layout import same_source_stamp
from guanlan_data.layout import resolve_data_path
from guanlan_data import sqlite as database
from contextlib import closing
from bisect import bisect_left, bisect_right
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
from guanlan_data.repositories.observer_series.store import canonical


def paths():
    from guanlan_data.repositories.observer_collections.config import results_path; from guanlan_data.repositories.observer_collections.config import support_path
    from guanlan_data.repositories.observer.config import settings
    from guanlan_data.repositories.industry_index.config import input_paths
    return {'stock':input_paths()[1], 'etf':resolve_data_path(Path(settings()['data_root']),'market_etf/industry_etf_observer.sqlite'),
            'support':support_path(), 'results':results_path()}


def digest(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def stamp(path):
    p=source_path(path)
    return [[str(f.absolute()),f.stat().st_size,f.stat().st_mtime_ns,f.stat().st_ino] if f.is_file()
            else [str(f.absolute()),None,None,None] for f in (p,Path(str(p)+'-wal'))]


def open_read(path):
    c=database.connect(source_path(path).as_uri()+'?mode=ro',uri=True,timeout=15)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    return c


def max_id(c,table):
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
    return c.execute('SELECT COALESCE(MAX(id),0) FROM '+table).fetchone()[0] if exists else 0


def source_marker(c,kind):
    if kind=='stock':
        identity=digest([tuple(r) for r in c.execute('SELECT ts_code,list_date,list_status FROM equity_master ORDER BY ts_code')])
        return {'runs':max_id(c,'tushare_sync_runs'),'revisions':max_id(c,'equity_source_revisions'),'identity':identity}
    row=c.execute('SELECT input_revision,data_revision FROM observer_meta WHERE singleton=1').fetchone()
    return {'input_revision':row[0],'data_revision':row[1],
            'run_rowid':c.execute('SELECT COALESCE(MAX(rowid),0) FROM collection_runs').fetchone()[0]}


def changes(c,kind,old,new):
    if old is None:return None
    if new==old:return {}
    if kind=='stock':
        if new['runs']<old['runs'] or new['revisions']<old['revisions'] or new['identity']!=old['identity']:return None
        result={}
        for r in c.execute('SELECT ts_code,trade_date FROM equity_source_revisions WHERE id>? ORDER BY id',(old['revisions'],)):
            result[r[0]]=min(result.get(r[0],r[1]),r[1])
        for r in c.execute("SELECT trade_date,status FROM tushare_sync_runs WHERE id>? AND run_type='trade_date' ORDER BY id",(old['runs'],)):
            if r['status']!='complete' or not r['trade_date']:return None
            for code,day in c.execute('SELECT ts_code,trade_date FROM equity_daily_raw WHERE trade_date=?',(r['trade_date'],)):
                result[code]=min(result.get(code,day),day)
        return result
    if new['input_revision']<old['input_revision'] or new['run_rowid']<old['run_rowid']:return None
    if new==old:return {}
    result={}
    rows=c.execute('SELECT parameters_json,status FROM collection_runs WHERE rowid>? ORDER BY rowid',(old['run_rowid'],)).fetchall()
    if not rows:return None
    for row in rows:
        if row['status']=='running':return None
        value=json.loads(row['parameters_json']);start=value.get('start') or value.get('trade_date') or value.get('end')
        codes=value.get('codes')
        if not start or not codes:return None
        if isinstance(codes,str):codes=codes.split(',')
        for code in codes:result[code]=min(result.get(code,start),start)
    return result


@lru_cache(maxsize=4096)
def calendar_used(calendar,start,end):
    # Cache the bounded content hash, not just hashing after rebuilding its input.
    dates=calendar.dates[bisect_left(calendar.dates,start):bisect_right(calendar.dates,end)]
    return digest(tuple((d,calendar.days[d]) for d in dates))


@lru_cache(maxsize=256)
def official_checksum(path,identity):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_official(ref):
    if not ref.get('official_sources'):return
    root=Path(ref['support_path']).parent.resolve()
    for item in ref.get('official_sources',()):
        path=(root/item['file']).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise ValueError('停牌证据已变化，请本地重算')
        identity=canonical(stamp(path))
        if official_checksum(str(path),identity)!=item['sha256']:raise ValueError('停牌证据已变化，请本地重算')
        if canonical(stamp(path))!=identity:raise ValueError('读取期间停牌证据发生变化，请重试')


@lru_cache(maxsize=128)
def _support_values(path,keys,identity):
    with closing(open_read(path)) as c:
        result={}
        for offset in range(0,len(keys),400):
            batch=keys[offset:offset+400]
            for r in c.execute('SELECT request_id,checksum,rows,endpoint FROM responses WHERE request_id IN ('+','.join('?' for _ in batch)+')',batch):
                result[r[0]]=[r[1],digest(json.loads(r[2])),r[3]]
        return result


def support_values(refs):
    requested={}
    for ref in refs.values():
        keys={r[0] for r in ref.get('support_requests',())}
        if keys:requested.setdefault(str(Path(ref['support_path']).absolute()),set()).update(keys)
    return {path:_support_values(path,tuple(sorted(keys)),canonical(stamp(path))) for path,keys in requested.items()}


def support_matches(ref,values):
    if not ref.get('support_requests'):return True
    path=str(Path(ref['support_path']).absolute())
    for key,expected in ref.get('support_requests',()):
        actual=values.get(path,{}).get(key)
        if not actual or actual[:2]!=[expected,expected]:return False
    return True


def read_rows(c,kind,code,start=None,end=None):
    if kind=='stock':
        sql='''SELECT r.trade_date,r.open,r.high,r.low,r.close,r.vol_lot*100 AS volume,
          r.amount_thousand_cny*1000 AS amount,f.adj_factor
          FROM equity_daily_raw r LEFT JOIN equity_adj_factor f USING(ts_code,trade_date)
          WHERE r.ts_code=?'''
    else:
        sql='SELECT trade_date,open,high,low,close,volume,amount FROM etf_daily WHERE etf_code=?'
    args=[code]
    if start:sql+=' AND '+('r.' if kind=='stock' else '')+'trade_date>=?';args.append(start)
    if end:sql+=' AND '+('r.' if kind=='stock' else '')+'trade_date<=?';args.append(end)
    return [dict(r) for r in c.execute(sql+' ORDER BY '+('r.' if kind=='stock' else '')+'trade_date',args)]


def active_references(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='publications'").fetchone():return {}
    rows=c.execute('''SELECT DISTINCT q.id,q.ref_json FROM current_publications p
      JOIN publication_members m ON m.publication_id=p.publication_id JOIN quote_refs q ON q.id=m.ref_id''')
    return {r[0]:json.loads(r[1]) for r in rows}


def receipt_signature(path):
    p=source_path(path)
    if not p.is_file():return None
    with closing(open_read(p)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='responses'").fetchone():return None
        # IDs/checksums only; arbitrary source file edits are fenced separately.
        return digest([tuple(r) for r in c.execute('SELECT request_id,checksum FROM responses ORDER BY request_id')])
