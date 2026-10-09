"""Read-only source adapter. A small archived holiday snapshot supplements central history."""
from guanlan_data import sqlite as database
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from guanlan_domain.observer_calendar.core import Calendar


def root():
    from guanlan_data.config import current
    return current().path('calendar_root')


def market_path():
    from guanlan_data.repositories.industry_index.config import input_paths
    return input_paths()[1]


def source_stamp(market=None, directory=None):
    folder = Path(directory) if directory is not None else root()
    path = Path(market) if market is not None else market_path()
    current=folder/'current.json'
    files = [path, Path(str(path) + '-wal'), current]
    if current.is_file():
        stat=current.stat()
        files.append(_active_path(str(folder.resolve()),stat.st_mtime_ns,stat.st_size))
    return tuple((str(p.absolute()), p.stat().st_mtime_ns, p.stat().st_size) if p.is_file()
                 else (str(p.absolute()), None, None) for p in files)


@lru_cache(maxsize=32)
def _active_path(folder,mtime,size):
    base=Path(folder)
    registry=json.loads((base/'current.json').read_text('utf-8'))
    path=(base/registry['snapshot']).resolve()
    if not path.is_relative_to((base/'snapshots').resolve()):raise ValueError('日历快照路径越界')
    return path


def _snapshot(folder):
    current = folder / 'current.json'
    if not current.is_file():
        return None
    registry = json.loads(current.read_text('utf-8'))
    if registry.get('schema_version') != 'observer-calendar.registry.v1':
        raise ValueError('日历注册版本无效')
    path = (folder / registry['snapshot']).resolve()
    if not path.is_relative_to((folder / 'snapshots').resolve()):
        raise ValueError('日历快照路径越界')
    payload = path.read_bytes()
    if len(payload) > 1048576 or hashlib.sha256(payload).hexdigest() != registry['snapshot_sha256']:
        raise ValueError('日历快照损坏，保留历史来源，请核对归藏')
    raw = json.loads(payload)
    if raw.get('schema_version') != 'observer-calendar.snapshot.v1':
        raise ValueError('日历快照版本无效')
    first, last = date.fromisoformat(raw['start']), date.fromisoformat(raw['end'])
    holidays = raw['holidays']
    if (first > last or (last-first).days > 55000 or not isinstance(holidays, list)
            or not holidays or len(holidays) != len(set(holidays)) or holidays != sorted(holidays)
            or any(date.fromisoformat(d).isoformat() != d or not raw['start'] <= d <= raw['end'] for d in holidays)):
        raise ValueError('日历快照范围或休市数据无效')
    return raw


_cache = {}


def load(market=None, *, connection=None, directory=None):
    path = Path(market) if market is not None else market_path()
    folder = Path(directory) if directory is not None else root()
    stamp = source_stamp(path, folder)
    key = (str(path.resolve()), str(folder.resolve()))
    cached = _cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    if connection is None:
        if path.is_file():
            with closing(database.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as c:
                c.execute('PRAGMA query_only=ON'); c.execute('BEGIN')
                rows = _rows(c)
        else:
            rows = []
    else:
        rows = _rows(connection)
    days = {}
    for d, opened in rows:
        if d in days or type(opened) is not int or opened not in (0, 1):
            raise ValueError('中央交易日历有重复或非法状态')
        date.fromisoformat(d); days[d] = opened
    sources = [{'kind': 'central_history', 'market': 'SSE', 'path': str(path)}]
    raw = _snapshot(folder)
    official_years = {}
    if raw:
        holidays = set(raw['holidays'])
        first, end = date.fromisoformat(raw['start']), date.fromisoformat(raw['end'])
        cursor = first
        while cursor <= end:
            d = cursor.isoformat()
            value = int(cursor.weekday() < 5 and d not in holidays)
            # Conflicting current history remains visibly unknown; never overwrite it.
            days[d] = value if d not in days or days[d] == value else None
            cursor += timedelta(days=1)
        sources.append(raw['source'])
        official_years = raw.get('official_years', {})
    revision = hashlib.sha256(json.dumps([sorted(days.items()), official_years], separators=(',', ':')).encode()).hexdigest()
    value = Calendar(days, revision=revision, sources=sources, official_years=official_years)
    if stamp != source_stamp(path, folder):
        raise ValueError('读取期间日历来源发生变化，请重试')
    if len(_cache) >= 16:
        _cache.pop(next(iter(_cache)),None)
    _cache[key] = (stamp, value)
    return value


def _rows(c):
    try:
        return c.execute("SELECT cal_date,is_open FROM trade_calendar WHERE exchange='SSE' ORDER BY cal_date").fetchall()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        return []


def status(day=None):
    from datetime import datetime, timezone
    day = day or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    calendar = load()
    return {'date': day, 'is_open': calendar.state(day), 'previous_open': calendar.previous(day),
            'latest_open': calendar.previous(day, inclusive=True), 'next_open': calendar.next(day),
            'coverage_start': calendar.coverage_start, 'coverage_end': calendar.coverage_end,
            'revision': calendar.revision, 'source': '中央历史' + (' + exchange_calendars归藏' if len(calendar.sources)>1 else ''),
            'reference_market': 'SSE', 'official_market_years': calendar.official_years,
            'unknown_days': sum(v is None for v in calendar.days.values())}
