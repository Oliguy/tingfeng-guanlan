"""Public data adapter: security identity, independent of UI or membership.

Only the explicit update worker fills supplemental identity evidence. Price and
classification databases remain read-only; existing AXDATA profiles are reused.
"""
from guanlan_data import sqlite as database
import json
import re
import sqlite3
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import canonical

CODE = re.compile(r'\d{6}\.(SH|SZ|BJ)\Z')
FIELDS = 'ts_code,name,list_status,list_date,delist_date'


def identities(codes, market, business=None, support=None):
    codes = sorted(set(codes))
    if any(not CODE.fullmatch(c) for c in codes):
        raise ValueError('证券代码无效')
    result = {}
    for path, table, field, provider in ((market, 'equity_master', 'ts_code', '行情证券资料'),
                                        (business, 'sp_securities', 'code', '企业资料')):
        if not path or not Path(path).is_file():
            continue
        with readonly(path) as c:
            fields = {r[1] for r in c.execute('PRAGMA table_info(' + table + ')')}
            if not {field, 'name'} <= fields:
                continue
            for start in range(0, len(codes), 400):
                batch = codes[start:start + 400]
                for row in c.execute('SELECT * FROM ' + table + ' WHERE ' + field + ' IN (' + ','.join('?' for _ in batch) + ')', batch):
                    data = dict(row)
                    if str(data.get('name') or '').strip() and data[field] not in result:
                        result[data[field]] = {**data, 'name':data['name'].strip(), 'name_source':provider}
            if table == 'sp_securities' and c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_aliases'").fetchone():
                missing = [code for code in codes if code not in result]
                for start in range(0, len(missing), 400):
                    batch = missing[start:start+400]
                    for row in c.execute('SELECT a.code,s.name FROM sp_aliases a JOIN sp_securities s ON s.id=a.security_id WHERE a.code IN (' + ','.join('?' for _ in batch) + ')',batch):
                        if row['name'] and row['code'] not in result:
                            result[row['code']]={'name':row['name'],'name_source':'企业资料代码沿革'}
    if support and Path(support).is_file() and any(code not in result for code in codes):
        with readonly(support) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='observer_security_names'").fetchone():
                for start in range(0, len(codes), 400):
                    batch = codes[start:start + 400]
                    for row in c.execute('SELECT * FROM observer_security_names WHERE code IN (' + ','.join('?' for _ in batch) + ')', batch):
                        if row['code'] in result:
                            continue
                        data = json.loads(row['payload'])
                        if digest(data) != row['checksum']:
                            raise ValueError('补充证券资料校验失败')
                        result[row['code']] = {**data, 'name_source':row['provider']}
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='responses'").fetchone():
                keys={digest(['stock_basic',{'ts_code':code,'fields':FIELDS}]):code for code in codes if code not in result}
                ids=list(keys)
                for start in range(0,len(ids),400):
                    batch=ids[start:start+400]
                    for row in c.execute('SELECT request_id,rows,checksum FROM responses WHERE request_id IN (' + ','.join('?' for _ in batch) + ')',batch):
                        values=json.loads(row['rows'])
                        if digest(values)!=row['checksum']:raise ValueError('证券资料来源校验失败')
                        code=keys[row['request_id']]
                        matches=[r for r in values if r.get('ts_code')==code and str(r.get('name') or '').strip()]
                        if len(matches)==1:result[code]={**matches[0],'name_source':'stock_basic 已采集证据'}
    return result


def run(params=None, *, market=None, business=None, support=None, client_factory=None, progress=None, cancel=None):
    """Fill only unresolved identities in the latest collected market universe."""
    from guanlan_data.repositories.industry_index.config import input_paths
    from guanlan_data.repositories.observer_collections.config import support_path
    from guanlan_data.repositories.industry_index.support import EvidenceClient
    from guanlan_data.repositories.industry_index.store import writer_lock
    b, m = input_paths()
    market, business, support = Path(market or m), Path(business or b), Path(support or support_path())
    with readonly(market) as c:
        day = c.execute('SELECT MAX(trade_date) FROM equity_daily_raw').fetchone()[0]
        codes = [r[0] for r in c.execute('SELECT ts_code FROM equity_daily_raw WHERE trade_date=?', (day,))]
    known = identities(codes, market, business, support)
    pending = [code for code in codes if code not in known]
    failed, filled = [], []
    if progress:
        progress(f'已有名称 {len(known)}只；定向补齐 {len(pending)}只')
    if pending:
        # One existing evidence store and its writer lock; no new source database.
        with writer_lock(support.with_name('security_names')):
            client = (client_factory or EvidenceClient)(support)
            for index, code in enumerate(pending):
                if cancel and cancel():
                    return {'status':'cancelled', 'filled':filled, 'remaining':pending[index:], 'as_of':day}
                try:
                    rows = client.request('stock_basic', _refresh=True, ts_code=code, fields=FIELDS)
                    valid = [r for r in rows if r.get('ts_code') == code and str(r.get('name') or '').strip()]
                    if len(valid) != 1:
                        raise ValueError('来源未返回唯一有效证券名称')
                    data = {k:valid[0].get(k) for k in ('ts_code','name','list_status','list_date','delist_date')}
                    data['name'] = data['name'].strip()
                    with closing(database.connect(support, timeout=30)) as c, c:
                        c.execute('CREATE TABLE IF NOT EXISTS observer_security_names (code TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL, provider TEXT NOT NULL, fetched_at TEXT NOT NULL)')
                        c.execute('INSERT OR REPLACE INTO observer_security_names VALUES (?,?,?,?,?)',
                                  (code, canonical(data), digest(data), 'stock_basic', datetime.now(timezone.utc).isoformat()))
                    filled.append(code)
                    if progress:
                        progress(f'补齐 {len(filled)}/{len(pending)}：{data["name"]} {code}')
                except (ValueError, OSError, sqlite3.Error) as exc:
                    failed.append({'name':code,'message':str(exc)})
    resolved = identities(codes, market, business, support)
    unresolved = [code for code in codes if code not in resolved]
    return {'status':'partial' if unresolved else 'succeeded', 'as_of':day, 'reused':len(known),
            'filled':filled, 'remaining':unresolved, 'failed':failed,
            'warning':f'复用已有名称 {len(known)}只，补齐 {len(filled)}只，剩余 {len(unresolved)}只'}
