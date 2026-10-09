"""Bounded read-only market inputs; values and source revisions are fingerprinted."""
from guanlan_data import sqlite as database
from contextlib import contextmanager
from datetime import date, timedelta, datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@contextmanager
def readonly(path):
    c = database.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON'); c.execute('BEGIN')
        yield c
    finally:
        c.rollback(); c.close()


def iso(value):
    if value is None: return None
    text = str(value)
    if len(text) == 8 and text.isdigit(): text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return date.fromisoformat(text).isoformat()


def year_before(day):
    d = date.fromisoformat(day)
    try: return d.replace(year=d.year - 1).isoformat()
    except ValueError: return d.replace(year=d.year - 1, day=28).isoformat()


def read_market(path, codes, *, end_date, start_date=None):
    """One transaction, requested members; fixed history stabilizes ETF indicators."""
    end = iso(end_date)
    start = iso(start_date) if start_date else year_before(end)
    if start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > 366:
        raise ValueError('INVALID_ONE_YEAR_RANGE')
    codes = sorted(set(codes))
    if len(codes) > 10000: raise ValueError('TOO_MANY_MEMBERS')
    with readonly(path) as c:
        latest = c.execute('SELECT MAX(trade_date) FROM equity_daily_raw').fetchone()[0]
        if latest is None: raise ValueError('MARKET_EMPTY')
        actual_end = min(end, latest)
        calculation_start = min('2024-01-02', start)
        from guanlan_data.repositories.observer_calendar import load as load_calendar
        shared_calendar=load_calendar(path,connection=c)
        warm_start=(date.fromisoformat(calculation_start)-timedelta(days=90)).isoformat()
        calendar=[{'exchange':'SSE','cal_date':d,'is_open':v} for d,v in sorted(shared_calendar.days.items()) if warm_start<=d<=actual_end]
        if not calendar or calendar[-1]['cal_date'] != actual_end:
            raise ValueError('CALENDAR_COVERAGE_MISSING')
        # Every calendar day within the requested part must be represented.
        expected = (date.fromisoformat(actual_end) - date.fromisoformat(calculation_start)).days + 1
        if expected < 1 or sum(r['cal_date'] >= calculation_start for r in calendar) != expected:
            raise ValueError('CALENDAR_RANGE_INCOMPLETE')
        if not shared_calendar.covered(calculation_start,actual_end):raise ValueError('CALENDAR_RANGE_INCOMPLETE')
        opened = [r['cal_date'] for r in calendar if r['is_open'] == 1]
        display = [d for d in opened if d >= start]
        before = [d for d in opened if d < calculation_start][-21:]
        if not display or len(before) < 21: raise ValueError('CALENDAR_WARMUP_MISSING')
        calculation_dates = [d for d in opened if d >= calculation_start]
        dates = before + calculation_dates
        # Extend only the leading preheat boundary when a listed member is already halted.
        # Require the whole intervening session chain to be evidenced; never jump a missing day.
        for code in codes:
            meta = c.execute('SELECT list_date,list_status FROM equity_master WHERE ts_code=?', (code,)).fetchone()
            if not meta or meta['list_status'] != 'L' or iso(meta['list_date']) > dates[0]: continue
            if c.execute('SELECT 1 FROM equity_daily_raw WHERE ts_code=? AND trade_date=?',(code,dates[0])).fetchone(): continue
            seed = c.execute('SELECT MAX(trade_date) FROM equity_daily_raw WHERE ts_code=? AND trade_date<?',(code,dates[0])).fetchone()[0]
            if seed and seed >= opened[0]: dates = [d for d in opened if seed <= d <= actual_end]
        quotes, metadata, sources = {}, {}, hashlib.sha256()
        source_latest = None
        for code in codes:
            meta = c.execute('SELECT * FROM equity_master WHERE ts_code=?', (code,)).fetchone()
            metadata[code] = dict(meta) if meta else None
            rows = [dict(r) for r in c.execute('''SELECT r.trade_date,r.open,r.high,r.low,r.close,
                r.vol_lot,r.amount_thousand_cny,r.source_payload_sha256 AS price_hash,
                r.fetched_at AS price_fetched_at,f.adj_factor,
                f.source_payload_sha256 AS factor_hash,f.fetched_at AS factor_fetched_at
                FROM equity_daily_raw r LEFT JOIN equity_adj_factor f
                ON f.ts_code=r.ts_code AND f.trade_date=r.trade_date
                WHERE r.ts_code=? AND r.trade_date BETWEEN ? AND ? ORDER BY r.trade_date''',
                (code, dates[0], actual_end))]
            sources.update(canonical([code, metadata[code], rows]).encode())
            for row in rows:
                updates = [row.get(k) for k in ('price_fetched_at', 'factor_fetched_at') if row.get(k)]
                if updates: source_latest = max([source_latest or '', *updates])
            quotes[code] = {r['trade_date']: r for r in rows}
        return {'dates': dates, 'display_dates': display, 'calculation_dates': calculation_dates, 'start_date': start,
                'requested_end': end, 'actual_end': actual_end, 'market_latest': latest,
                'calendar_source': 'observer_shared_SSE_calendar_v1',
                'calendar_hash': digest(calendar), 'market_hash': sources.hexdigest(),
                'market_vintage': 'current_stored', 'source_latest_at': source_latest,
                'metadata': metadata, 'quotes': quotes}
