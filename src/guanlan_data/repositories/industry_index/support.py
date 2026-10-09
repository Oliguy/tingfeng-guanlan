"""Explicit-update-only, bounded official evidence cache. Source DBs stay read-only."""
from guanlan_data import sqlite as database
from contextlib import closing
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import time
from guanlan_data.repositories.industry_index.inputs import canonical; from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import iso


class EvidenceClient:
    def __init__(self, path, provider=None, online=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(database.connect(self.path)) as c:
            c.execute('CREATE TABLE IF NOT EXISTS responses (request_id TEXT PRIMARY KEY, endpoint TEXT, params TEXT, rows TEXT, checksum TEXT, fetched_at TEXT)')
            c.commit()
        self.provider, self.online, self.last_request = provider, online, 0.
        self.receipts = {}
        self.network_calls = 0

    def request(self, endpoint, *, _refresh=False, **params):
        if endpoint not in {'daily', 'adj_factor', 'suspend_d', 'stock_basic'}:
            raise ValueError('UNSUPPORTED_SUPPORT_ENDPOINT')
        codes = params.get('ts_code', '').split(',')
        if not codes or len(codes) > 5 or any(len(code) != 9 for code in codes):
            raise ValueError('UNBOUNDED_SUPPORT_REQUEST')
        key = digest([endpoint, params])
        with closing(database.connect(self.path)) as c:
            cached = c.execute('SELECT rows,checksum FROM responses WHERE request_id=?', (key,)).fetchone()
        if cached and not _refresh:
            rows = json.loads(cached[0])
            if digest(rows) != cached[1]: raise ValueError('SUPPORT_CACHE_CORRUPT')
        else:
            if not self.online: raise ValueError('SUPPORT_NOT_CACHED')
            if self.provider is None:raise ValueError('需要显式证据提供者；界面更新请连接 provider 服务')
            time.sleep(max(0, 1.02 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                frame = self.provider.query(endpoint, **params)
                rows = json.loads(frame.to_json(orient='records', force_ascii=False))
            except Exception:
                raise ValueError('SUPPORT_PROVIDER_FAILED:' + endpoint) from None
            self.network_calls += 1
            if len(rows) >= 5000: raise ValueError('SUPPORT_POSSIBLY_TRUNCATED')
            for row in rows:
                if row.get('ts_code') not in codes: raise ValueError('SUPPORT_UNEXPECTED_SECURITY')
                day = row.get('trade_date')
                if day and not params['start_date'] <= day <= params['end_date']:
                    raise ValueError('SUPPORT_UNEXPECTED_DATE')
            with closing(database.connect(self.path)) as c:
                c.execute('INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?)',
                          (key, endpoint, canonical(params), canonical(rows), digest(rows), datetime.now(timezone.utc).isoformat()))
                c.commit()
        self.receipts[key] = digest(rows)
        return rows


def missing_sessions(market):
    result = {}
    for code, meta in market['metadata'].items():
        if not meta or meta.get('list_status') != 'L': continue
        listing = iso(meta['list_date'])
        history = market['quotes'].get(code, {})
        missing = [day for day in market['dates'] if day >= listing and day not in history]
        if missing: result[code] = missing
    return result


def _chunks(values, size=5):
    values = sorted(values)
    for i in range(0, len(values), size): yield values[i:i+size]


def official_events(path):
    file = Path(path).with_name('official_events.json')
    if not file.exists(): return {'halts': [], 'aliases': [], 'sources': []}
    data = json.loads(file.read_text(encoding='utf-8'))
    if data.get('schema_version') != 'industry30.official_events.v1': raise ValueError('INVALID_OFFICIAL_EVENTS')
    known = set()
    for source in data['sources']:
        document = (file.parent / source['file']).resolve()
        if not document.is_relative_to(file.parent.resolve()): raise ValueError('OFFICIAL_SOURCE_OUTSIDE_SUPPORT')
        if hashlib.sha256(document.read_bytes()).hexdigest() != source['sha256']: raise ValueError('OFFICIAL_SOURCE_CHANGED')
        known.add(source['file'])
    for event in data['halts'] + data['aliases']:
        if event['source_file'] not in known: raise ValueError('OFFICIAL_EVENT_WITHOUT_SOURCE')
    return data


def repair_inputs(market, cache_path, *, online=True, provider=None, progress=None, client_factory=None):
    client = (client_factory or EvidenceClient)(cache_path, provider, online)
    reviewed = official_events(cache_path)
    fixed_identity = []
    for code, meta in market['metadata'].items():
        if meta and meta.get('list_date'): continue
        rows = client.request('stock_basic', ts_code=code, fields='ts_code,name,list_status,list_date,delist_date')
        if len(rows) == 1:
            market['metadata'][code] = {**rows[0], 'support_hash': digest(rows)}
            fixed_identity.append(code)
    missing = missing_sessions(market)
    suspensions = {}
    for batch in _chunks(missing):
        begin = min(day for code in batch for day in missing[code])
        end = max(day for code in batch for day in missing[code])
        rows = client.request('suspend_d', ts_code=','.join(batch), start_date=begin.replace('-', ''), end_date=end.replace('-', ''))
        for row in rows:
            if row['suspend_type'] == 'S' and not row.get('suspend_timing'):
                suspensions.setdefault(row['ts_code'], {})[iso(row['trade_date'])] = digest(row)
        if progress: progress('suspensions', len(client.receipts), client.network_calls)
    for alias in reviewed['aliases']:
        code = alias['code']
        days = [d for d in missing.get(code, []) if d not in suspensions.get(code, {})]
        if not days: continue
        rows = client.request('suspend_d', ts_code=alias['previous_code'], start_date=min(days).replace('-', ''), end_date=max(days).replace('-', ''))
        for row in rows:
            if row['suspend_type'] == 'S' and not row.get('suspend_timing'):
                suspensions.setdefault(code, {})[iso(row['trade_date'])] = digest([row, alias])
    for halt in reviewed['halts']:
        for day in missing.get(halt['code'], []):
            if halt['start'] <= day <= halt['end']:
                suspensions.setdefault(halt['code'], {})[day] = digest(halt)
    # Some provider multi-code/date combinations return an empty subset without error.
    # Recheck only unexplained holes individually; never interpret silence as suspension.
    for code, days in missing.items():
        pending = [d for d in days if d not in suspensions.get(code, {})]
        if not pending: continue
        rows = client.request('suspend_d', ts_code=code, start_date=min(pending).replace('-', ''), end_date=max(pending).replace('-', ''))
        for row in rows:
            if row['suspend_type'] == 'S' and not row.get('suspend_timing'):
                suspensions.setdefault(code, {})[iso(row['trade_date'])] = digest(row)
    unresolved = {code: [d for d in days if d not in suspensions.get(code, {})] for code, days in missing.items()}
    unresolved = {code: days for code, days in unresolved.items() if days}
    repaired_prices, repaired_factors = [], []
    for batch in _chunks(unresolved):
        begin = min(day for code in batch for day in unresolved[code])
        end = max(day for code in batch for day in unresolved[code])
        rows = client.request('daily', ts_code=','.join(batch), start_date=begin.replace('-', ''), end_date=end.replace('-', ''))
        returned = {(r['ts_code'], iso(r['trade_date'])) for r in rows}
        for code in batch:
            pending = [d for d in unresolved[code] if (code, d) not in returned]
            if pending and len(batch) > 1:
                rows += client.request('daily', ts_code=code, start_date=min(pending).replace('-', ''), end_date=max(pending).replace('-', ''))
        for row in rows:
            code, day = row['ts_code'], iso(row['trade_date'])
            if day in unresolved[code]:
                market['quotes'][code][day] = {k: row[k] for k in ('open', 'high', 'low', 'close')}
                market['quotes'][code][day].update(trade_date=day, vol_lot=row['vol'], amount_thousand_cny=row['amount'], price_hash=digest(row), source_kind='official_backfill')
                repaired_prices.append([code, day])
        if progress: progress('prices', len(client.receipts), client.network_calls)
    missing_factors = {}
    for code, history in market['quotes'].items():
        days = [day for day, r in history.items() if not r.get('adj_factor')]
        if days: missing_factors[code] = days
    for batch in _chunks(missing_factors):
        begin = min(day for code in batch for day in missing_factors[code])
        end = max(day for code in batch for day in missing_factors[code])
        rows = client.request('adj_factor', ts_code=','.join(batch), start_date=begin.replace('-', ''), end_date=end.replace('-', ''))
        for row in rows:
            code, day = row['ts_code'], iso(row['trade_date'])
            if day in missing_factors[code]:
                market['quotes'][code][day].update(adj_factor=row['adj_factor'], factor_hash=digest(row))
                repaired_factors.append([code, day])
        if progress: progress('factors', len(client.receipts), client.network_calls)
    market['suspensions'] = suspensions
    market['support'] = dict(requests=client.receipts, identity_repairs=fixed_identity,
                             official_events=reviewed,
                             price_repairs=repaired_prices, factor_repairs=repaired_factors,
                             confirmed_full_day_suspensions=sum(len(d) for d in suspensions.values()))
    market['support_hash'] = digest(market['support'])
    market['market_hash'] = digest([market['market_hash'], market['support_hash']])
    return market


def valuation_quotes(market):
    """Derived marks only: never write zero-volume suspension candles to raw inputs."""
    result = {}
    for code, raw in market['quotes'].items():
        history, previous = dict(raw), None
        for day in market['dates']:
            current = history.get(day)
            evidence = market.get('suspensions', {}).get(code, {}).get(day)
            if current is None and evidence and previous and previous.get('adj_factor') and previous.get('close'):
                # Carry adjusted value. A changed factor is applied on resumption, once.
                value = previous['close'] * previous['adj_factor']
                current = dict(trade_date=day, open=value, high=value, low=value, close=value,
                               adj_factor=1., vol_lot=0., amount_thousand_cny=0.,
                               valuation_kind='confirmed_suspension', suspension_hash=evidence)
                history[day] = current
            previous = current
        result[code] = history
    return result
