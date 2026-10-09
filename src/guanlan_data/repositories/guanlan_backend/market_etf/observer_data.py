"""Observer data contracts, revision tracking and deterministic calculations.

No network access. Callers supply the managed store and own its write lock.
Raw quotes and immutable grouping versions are never repaired by this module.
"""
from __future__ import annotations
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from guanlan_domain.guanlan_backend.market_etf.domain.primitives import RULE_VERSION; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import canonical; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import digest; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import normalize_memberships; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import daily_flow

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
SCHEMA = "\nCREATE TABLE IF NOT EXISTS observer_meta(\n singleton INTEGER PRIMARY KEY CHECK(singleton=1),input_revision INTEGER NOT NULL DEFAULT 0,\n data_revision INTEGER NOT NULL DEFAULT 0);\nINSERT OR IGNORE INTO observer_meta(singleton) VALUES(1);\nCREATE TABLE IF NOT EXISTS observer_dirty_ranges(\n group_id TEXT PRIMARY KEY,from_date TEXT NOT NULL,input_revision INTEGER NOT NULL,reason TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS observer_daily_quality(\n trade_date TEXT NOT NULL,group_id TEXT NOT NULL,computed_from_revision INTEGER NOT NULL,\n calendar_status TEXT NOT NULL,membership_status TEXT NOT NULL,quote_status TEXT NOT NULL,\n return_status TEXT NOT NULL,leader_status TEXT NOT NULL,flow_status TEXT NOT NULL,history_status TEXT NOT NULL,\n flow_covered_count INTEGER NOT NULL,flow_expected_count INTEGER NOT NULL,available_flow_subtotal REAL,\n return_coverage_ratio REAL NOT NULL,weights_revision INTEGER,reason_codes_json TEXT NOT NULL,\n membership_basis TEXT NOT NULL DEFAULT 'record_effective',PRIMARY KEY(trade_date,group_id));\nCREATE TABLE IF NOT EXISTS observer_monthly_baskets(\n group_id TEXT NOT NULL,month TEXT NOT NULL,revision INTEGER NOT NULL,is_current INTEGER NOT NULL,\n input_revision INTEGER NOT NULL,source_digest TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,\n PRIMARY KEY(group_id,month,revision));\nCREATE UNIQUE INDEX IF NOT EXISTS observer_basket_current ON observer_monthly_baskets(group_id,month) WHERE is_current=1;\nCREATE TABLE IF NOT EXISTS observer_share_observations(\n etf_code TEXT NOT NULL,as_of_date TEXT NOT NULL,source_hash TEXT NOT NULL,\n shares_outstanding REAL,nav REAL,nav_date TEXT,source_name TEXT,source_url TEXT,known_at TEXT,\n observation_kind TEXT NOT NULL,split_status TEXT NOT NULL,split_factor REAL,precision_json TEXT,\n fetched_at TEXT NOT NULL,PRIMARY KEY(etf_code,as_of_date,source_hash));\nCREATE TABLE IF NOT EXISTS observer_membership_archive(\n original_hash TEXT PRIMARY KEY,record_json TEXT NOT NULL,reason TEXT NOT NULL,archived_at TEXT NOT NULL);\n"

def _statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(';') if statement.strip()]

def ensure_schema(conn: sqlite3.Connection, *, triggers: bool=True) -> None:
    """Execute without executescript, preserving the caller's active transaction."""
    for statement in _statements(SCHEMA):
        conn.execute(statement)
    if triggers:
        for statement in trigger_statements():
            conn.execute(statement)

def trigger_statements() -> list[str]:
    result = []
    bump = 'UPDATE observer_meta SET input_revision=input_revision+1,data_revision=data_revision+1 WHERE singleton=1;'
    quote_fields = ('open', 'high', 'low', 'close', 'prev_close', 'volume', 'amount', 'shares_outstanding', 'nav', 'nav_date', 'quote_source', 'share_source')
    for table, prefix, changed in (('etf_daily', 'quote', ' OR '.join((f'OLD.{field} IS NOT NEW.{field}' for field in quote_fields))), ('etf_group_membership', 'membership', ' OR '.join((f'OLD.{field} IS NOT NEW.{field}' for field in ('group_id', 'etf_code', 'effective_from', 'effective_to', 'status', 'evidence_json')))), ('observer_share_observations', 'share', ' OR '.join((f'OLD.{field} IS NOT NEW.{field}' for field in ('shares_outstanding', 'nav', 'nav_date', 'observation_kind', 'split_status', 'split_factor', 'source_url'))))):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            refs = ('OLD', 'NEW') if event == 'UPDATE' else ('OLD',) if event == 'DELETE' else ('NEW',)
            actions = bump
            for ref in refs:
                if prefix == 'membership':
                    query = f"SELECT {ref}.group_id,{ref}.effective_from,input_revision,'membership' FROM observer_meta WHERE singleton=1"
                else:
                    day = f'{ref}.trade_date' if prefix == 'quote' else f'{ref}.as_of_date'
                    query = f"SELECT DISTINCT m.group_id,{day},o.input_revision,'{prefix}' FROM etf_group_membership m,observer_meta o WHERE m.etf_code={ref}.etf_code AND o.singleton=1"
                actions += f'INSERT INTO observer_dirty_ranges(group_id,from_date,input_revision,reason) {query} ON CONFLICT(group_id) DO UPDATE SET from_date=MIN(from_date,excluded.from_date),input_revision=excluded.input_revision,reason=excluded.reason;'
            when = f' WHEN {changed}' if event == 'UPDATE' else ''
            result.append(f'CREATE TRIGGER IF NOT EXISTS observer_{prefix}_{event.lower()} AFTER {event} ON {table}{when} BEGIN {actions} END')
    for event in ('INSERT', 'UPDATE', 'DELETE'):
        refs = ('OLD', 'NEW') if event == 'UPDATE' else ('OLD',) if event == 'DELETE' else ('NEW',)
        actions = 'UPDATE observer_meta SET data_revision=data_revision+1 WHERE singleton=1;'
        for ref in refs:
            actions += f"INSERT INTO observer_dirty_ranges SELECT {ref}.group_id,{ref}.leader_month||'-01',input_revision,'leader' FROM observer_meta WHERE singleton=1 ON CONFLICT(group_id) DO UPDATE SET from_date=MIN(from_date,excluded.from_date),input_revision=excluded.input_revision,reason=excluded.reason;"
        result.append(f'CREATE TRIGGER IF NOT EXISTS observer_leader_{event.lower()} AFTER {event} ON monthly_leaders BEGIN {actions} END')
    for table in ('etf_holdings', 'etf_focus_watchlist', 'industry_groups', 'etf_master'):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            result.append(f'CREATE TRIGGER IF NOT EXISTS observer_view_{table}_{event.lower()} AFTER {event} ON {table} BEGIN UPDATE observer_meta SET data_revision=data_revision+1 WHERE singleton=1; END')
    result.append("CREATE TRIGGER IF NOT EXISTS observer_master_dates_update AFTER UPDATE ON etf_master\n      WHEN OLD.listing_date IS NOT NEW.listing_date OR OLD.inception_date IS NOT NEW.inception_date\n      BEGIN UPDATE observer_meta SET input_revision=input_revision+1,data_revision=data_revision+1 WHERE singleton=1;\n      INSERT INTO observer_dirty_ranges SELECT group_id,min(effective_from),input_revision,'listing_date' FROM etf_group_membership,observer_meta WHERE etf_code=NEW.etf_code AND singleton=1 GROUP BY group_id\n      ON CONFLICT(group_id) DO UPDATE SET from_date=MIN(from_date,excluded.from_date),input_revision=excluded.input_revision,reason=excluded.reason;\n      END")
    return result

def install_membership_guards(conn: sqlite3.Connection) -> None:
    for event in ('INSERT', 'UPDATE'):
        exclusion = ' AND NOT (m.etf_code=OLD.etf_code AND m.group_id=OLD.group_id AND m.effective_from=OLD.effective_from)' if event == 'UPDATE' else ''
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS observer_membership_guard_{event.lower()}\n        BEFORE {event} ON etf_group_membership BEGIN\n        SELECT CASE WHEN NEW.effective_to IS NOT NULL AND NEW.effective_to<NEW.effective_from\n          THEN RAISE(ABORT,'invalid_membership_interval') END;\n        SELECT CASE WHEN EXISTS(SELECT 1 FROM etf_group_membership m WHERE m.etf_code=NEW.etf_code\n          AND MAX(m.effective_from,NEW.effective_from)<=MIN(COALESCE(m.effective_to,'9999-12-31'),COALESCE(NEW.effective_to,'9999-12-31')){exclusion})\n          THEN RAISE(ABORT,'overlapping_primary_membership') END;\n        END")

def calendar_dates(store: Any, start: str, end: str) -> tuple[list[str], str]:
    snapshot = store.calendar.snapshot(start, end)
    days = snapshot.dates(start, end)
    return (days, 'PASS' if days else 'MISSING')

def refresh_calendar_revision(store: Any) -> bool:
    """Detect changes to the managed calendar before rebuilding derived data.

    Calendar storage has a separate owner, so a source fingerprint complements
    the quote/membership SQLite triggers. It never changes that source database.
    """
    snapshot = store.calendar.snapshot()
    if snapshot.source == 'missing':
        return False
    signature = snapshot.version
    with store.transaction() as conn:
        previous = conn.execute("SELECT value FROM schema_meta WHERE key='observer_calendar_digest'").fetchone()
        if previous and previous[0] == signature:
            return False
        conn.execute("INSERT INTO schema_meta VALUES('observer_calendar_digest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (signature,))
        conn.execute('UPDATE observer_meta SET input_revision=input_revision+1,data_revision=data_revision+1 WHERE singleton=1')
        conn.execute("INSERT INTO observer_dirty_ranges\n        SELECT group_id,min(effective_from),input_revision,'calendar' FROM etf_group_membership,observer_meta WHERE singleton=1 GROUP BY group_id\n        ON CONFLICT(group_id) DO UPDATE SET from_date=MIN(from_date,excluded.from_date),input_revision=excluded.input_revision,reason=excluded.reason")
    return True

def record_share_observations(conn: sqlite3.Connection, rows: Sequence[Mapping[str, Any]]) -> int:
    changed = 0
    for row in rows:
        code = str(row['etf_code']).zfill(6)
        day = str(row.get('as_of_date') or row.get('trade_date'))
        date.fromisoformat(day)
        shares = row.get('shares_outstanding')
        nav = row.get('nav')
        if nav is None and shares and (row.get('net_assets') is not None):
            nav = float(row['net_assets']) / float(shares)
        source_hash = row.get('source_hash') or 'unverified:' + digest({k: row.get(k) for k in ('etf_code', 'as_of_date', 'trade_date', 'shares_outstanding', 'nav', 'net_assets', 'source_name', 'share_source')})
        values = (code, day, source_hash, shares, nav, row.get('nav_date') or (day if nav is not None else None), row.get('source_name') or row.get('share_source'), row.get('source_url'), row.get('known_at'), row.get('observation_kind', 'disclosure'), row.get('split_status', 'unknown'), row.get('split_factor'), canonical(row.get('precision')), row.get('fetched_at') or now())
        before = conn.total_changes
        conn.execute('INSERT INTO observer_share_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)\n        ON CONFLICT(etf_code,as_of_date,source_hash) DO UPDATE SET\n        shares_outstanding=excluded.shares_outstanding,nav=excluded.nav,nav_date=excluded.nav_date,\n        source_name=excluded.source_name,source_url=excluded.source_url,known_at=excluded.known_at,\n        observation_kind=excluded.observation_kind,split_status=excluded.split_status,split_factor=excluded.split_factor\n        WHERE shares_outstanding IS NOT excluded.shares_outstanding OR nav IS NOT excluded.nav\n          OR nav_date IS NOT excluded.nav_date OR source_url IS NOT excluded.source_url\n          OR observation_kind IS NOT excluded.observation_kind OR split_status IS NOT excluded.split_status\n          OR split_factor IS NOT excluded.split_factor', values)
        changed += int(conn.total_changes > before)
    return changed

def share_observations(conn: sqlite3.Connection, codes: Sequence[str]) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    if not codes:
        return output
    for row in conn.execute(f"SELECT * FROM observer_share_observations WHERE etf_code IN ({','.join(('?' for _ in codes))}) ORDER BY fetched_at,source_hash", tuple(codes)):
        item = dict(row)
        old = output[item['etf_code']].get(item['as_of_date'])
        if old and (old['shares_outstanding'], old['nav']) != (item['shares_outstanding'], item['nav']):
            item['conflict'] = True
        output[item['etf_code']][item['as_of_date']] = item
    return output

def share_intervals(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    observed = share_observations(conn, [code]).get(code, {})
    result = []
    previous = None
    for day, current in sorted(observed.items()):
        if current.get('shares_outstanding') is None:
            continue
        if previous:
            delta = float(current['shares_outstanding']) - float(previous['shares_outstanding'])
            valid_nav = current.get('nav') if current.get('nav_date') == day else None
            result.append({'etf_code': code, 'period_start': previous['as_of_date'], 'period_end': day, 'share_change': delta, 'end_nav': valid_nav, 'end_value_estimate': delta * float(valid_nav) if valid_nav is not None else None, 'known_at': current.get('known_at'), 'observation_refs': [previous['source_hash'], current['source_hash']], 'label': '披露区间份额变化（期末净值估值）', 'status': 'CONFLICT' if current.get('conflict') or previous.get('conflict') else 'DISCLOSURE_INTERVAL'})
        previous = current
    return result

def revision_state(conn: sqlite3.Connection) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='observer_meta'").fetchone():
        return {'input_revision': 0, 'data_revision': 0, 'computed_from_revision': None, 'dirty_ranges': [], 'schema_status': 'UNMIGRATED'}
    result = dict(conn.execute('SELECT input_revision,data_revision FROM observer_meta WHERE singleton=1').fetchone())
    result['dirty_ranges'] = [dict(row) for row in conn.execute('SELECT * FROM observer_dirty_ranges ORDER BY group_id')]
    result['computed_from_revision'] = conn.execute('SELECT min(computed_from_revision) FROM observer_daily_quality').fetchone()[0]
    result['schema_status'] = 'PASS'
    return result

def overlay_quality(conn: sqlite3.Connection, rows: Sequence[Mapping[str, Any]], revision: Mapping[str, Any] | None=None) -> list[dict[str, Any]]:
    state = dict(revision) if isinstance(revision, Mapping) else revision_state(conn)
    dirty = {row['group_id']: row['from_date'] for row in state.get('dirty_ranges', [])}
    quality = {}
    if state.get('schema_status') != 'UNMIGRATED' and rows:
        keys = sorted({(str(row['group_id']), str(row['trade_date'])) for row in rows if row.get('group_id') and row.get('trade_date')})
        for offset in range(0, len(keys), 400):
            batch = keys[offset:offset + 400]
            wanted = ','.join(('(?,?)' for _ in batch))
            parameters = tuple((value for key in batch for value in key))
            for item in conn.execute(f'WITH wanted(group_id,trade_date) AS (VALUES {wanted}) SELECT q.* FROM observer_daily_quality q JOIN wanted USING(group_id,trade_date)', parameters):
                quality[item['group_id'], item['trade_date']] = dict(item)
    result = []
    for source in rows:
        row = dict(source)
        detail = quality.get((row.get('group_id'), row.get('trade_date')))
        if detail:
            row.update(detail)
            row['reason_codes'] = json.loads(row.pop('reason_codes_json', '[]'))
        elif row.get('trade_date'):
            row.update(quality_status='STALE', reason_codes=['derived_quality_unverified'])
        start = dirty.get(row.get('group_id'))
        if start and str(row.get('trade_date') or '9999-12-31') >= start or row.get('quality_status') == 'STALE':
            row['quality_status'] = 'STALE'
            row['reason_codes'] = sorted(set(row.get('reason_codes', [])) | {'derived_recalculation_pending'})
            for key in ('aggregate_amount', 'aggregate_volume', 'relative_amount_20d', 'estimated_net_subscription', 'available_flow_subtotal', 'group_return', 'synthetic_index'):
                row[key] = None
        result.append(row)
    return result

def recompute_group(store: Any, group_id: str, start: str, end: str) -> int:
    from guanlan_domain.guanlan_backend.market_etf.domain.calculation import calculate_group
    inputs = store.derived.inputs(group_id, end)
    first = min([start, *(row['effective_from'] for row in inputs['members'])])
    calendar_start = (date.fromisoformat(first).replace(day=1) - timedelta(days=40)).isoformat()
    snapshot = store.calendar.snapshot(calendar_start, end)
    timestamp = now()
    calculated = calculate_group(group_id=group_id, start=start, end=end, **{key: value for key, value in inputs.items() if key != 'data_revision'}, calendar=snapshot.dates(calendar_start, end), timestamp=timestamp)
    return store.derived.publish(group_id, end, inputs, calculated, calendar=store.calendar, calendar_snapshot=snapshot, timestamp=timestamp)

def validate_group(store: Any, group_id: str, start: str, end: str) -> dict[str, Any]:
    """Independent oracle: keyed membership/quotes, not production aggregate SQL."""
    days, calendar_status = calendar_dates(store, start, end)
    conn = store.connect(readonly=True)
    try:
        memberships = [dict(r) for r in conn.execute('SELECT * FROM etf_group_membership WHERE group_id=?', (group_id,))]
        codes = {r['etf_code'] for r in memberships}
        raw = {(r['etf_code'], r['trade_date']): dict(r) for r in conn.execute('SELECT etf_code,trade_date,amount FROM etf_daily WHERE trade_date<=?', (end,)) if r['etf_code'] in codes}
        listing_dates = {r['etf_code']: r['listing_date'] for r in conn.execute('SELECT etf_code,listing_date FROM etf_master')}
        stored = {r['trade_date']: dict(r) for r in conn.execute('SELECT * FROM industry_daily WHERE group_id=? AND trade_date BETWEEN ? AND ?', (group_id, start, end))}
        checks, gaps, duplicates, partial = ([], [], [], [])
        if calendar_status != 'PASS':
            days = sorted({day for _, day in raw if start <= day <= end})
        for day in days:
            expected_rows = [r for r in memberships if r['effective_from'] <= day <= (r.get('effective_to') or '9999-12-31') and (not listing_dates.get(r['etf_code']) or listing_dates[r['etf_code']] <= day)]
            expected = {r['etf_code'] for r in expected_rows}
            if not expected:
                continue
            if len(expected_rows) != len(expected):
                duplicates.append(day)
            row = stored.get(day)
            if not row:
                gaps.append(day)
                continue
            amounts = [raw.get((code, day), {}).get('amount') for code in expected]
            complete = all((amount is not None for amount in amounts))
            subtotal = sum((float(amount) for amount in amounts if amount is not None)) if any((amount is not None for amount in amounts)) else None
            if (subtotal is None) != (row['aggregate_amount'] is None) or (subtotal is not None and (not math.isclose(subtotal, row['aggregate_amount'], rel_tol=1e-09, abs_tol=1))):
                checks.append({'trade_date': day, 'expected_amount': subtotal, 'actual_amount': row['aggregate_amount']})
            if not complete:
                partial.append(day)
        dirty = [r for r in revision_state(conn)['dirty_ranges'] if r['group_id'] == group_id and r['from_date'] <= end]
        failed = calendar_status != 'PASS' or bool(checks or gaps or duplicates or partial or dirty) or (not stored)
        return {'status': 'FAIL' if failed else 'PASS', 'group_id': group_id, 'start': start, 'end': end, 'calendar_status': calendar_status, 'aggregate_mismatch_count': len(checks), 'aggregate_mismatches': checks[:20], 'missing_group_days': gaps, 'duplicate_membership_days': duplicates, 'duplicate_daily_keys': 0, 'partial_days': len(partial), 'dirty_ranges': dirty, 'checked_days': len(stored), 'scope': 'independent_membership_quote_and_output_integrity; flow_and_returns_are_separate_dimensions'}
    finally:
        conn.close()
