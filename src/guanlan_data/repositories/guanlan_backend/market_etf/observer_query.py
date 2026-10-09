"""Bounded ETF read models; never initialize, collect, or modify a database."""
from __future__ import annotations
from guanlan_data.layout import resolve_data_path
from guanlan_data import sqlite as database
import copy
import json
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from guanlan_domain.contracts import OperationError
from guanlan_data.repositories.guanlan_backend.market_etf.observer_data import digest
from guanlan_data.repositories.guanlan_backend.market_etf.schema_version import require_schema; from guanlan_data.repositories.guanlan_backend.market_etf.schema_version import SchemaCompatibilityError
from guanlan_data.repositories.guanlan_backend.market_etf.observer_read_repository import ObserverReadRepository
from guanlan_domain.guanlan_backend.market_etf.domain.primitives import continuous_adjust_bars
from guanlan_domain.guanlan_backend.market_etf.domain.chart import chart_rows
from guanlan_data.repositories.guanlan_backend.market_etf.calendar_repository import CalendarRepository
from guanlan_domain.guanlan_backend.market_etf.query_dto import SCHEMA; from guanlan_domain.guanlan_backend.market_etf.query_dto import compact_detail
METHOD = 'etf_observer_read_v2'
BAR_FIELDS = {'trade_date', 'open', 'high', 'low', 'close', 'prev_close', 'volume', 'amount', 'z_zhixing_short_trend', 'z_zhixing_bull_bear', 'is_adjustment_boundary', 'adjustment_factor'}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def week_start(value: str) -> str:
    day = date.fromisoformat(value)
    return (day - timedelta(days=day.weekday())).isoformat()

def weekly_rows(rows: list[dict]) -> list[dict]:
    weeks: list[dict] = []
    for row in rows:
        key = week_start(row['trade_date'])
        if not weeks or weeks[-1]['week_start'] != key:
            weeks.append({**row, 'week_start': key, 'period_start': row['trade_date']})
        else:
            item = weeks[-1]
            item.update(trade_date=row['trade_date'], close=row['close'], high=max(item['high'] or 0, row['high'] or 0), low=min(item['low'] or 0, row['low'] or 0), volume=(item.get('volume') or 0) + (row.get('volume') or 0), amount=(item.get('amount') or 0) + (row.get('amount') or 0))
    return weeks

def weekly_strength(rows: list[dict]) -> dict:
    weeks = weekly_rows(rows)
    recent = []
    for index in range(max(29, len(weeks) - 5), len(weeks)):
        closes = [item.get('close') for item in weeks[index - 29:index + 1]]
        if any((value is None for value in closes)):
            continue
        avg = sum(closes) / 30
        recent.append((weeks[index]['close'], avg))
    last = weeks[-1] if weeks else {}
    close, avg = recent[-1] if recent else (None, None)
    above = sum((value > mean for value, mean in recent))
    total = len(recent)
    above_now = close > avg if close is not None and avg else None
    previous = weeks[-2].get('close') if len(weeks) > 1 else None
    return {'above': above, 'total': total, 'available': len(weeks), 'strength_score': above if total == 5 else None, 'above_now': above_now, 'distance_30w': close / avg - 1 if close is not None and avg else None, 'weekly_return': last.get('close') / previous - 1 if last.get('close') is not None and previous else None, 'shade': min(0.3, 0.07 + (above if above_now else total - above) * 0.046) if total == 5 else 0, 'weekly_volume': last.get('volume'), 'as_of_date': last.get('trade_date'), 'week_start': last.get('week_start'), 'is_partial_week': bool(last and date.fromisoformat(last['trade_date']).weekday() < 4), 'method_version': 'ma30w_asof_day_v1'}

class _BorrowedConnection:

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        pass

class EtfObserverQueries:

    def __init__(self, data_root: Path, *, ttl_seconds: float=5):
        self.root = Path(data_root).resolve()
        self.path = resolve_data_path(self.root,'market_etf/industry_etf_observer.sqlite')
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._conn = None
        self._identity = None
        self._market_conn = None
        self._market_identity = None
        self._snapshot_active = False
        self._pinned_market = None
        self._session = uuid.uuid4().hex
        self._cache: dict[str, tuple[float, dict]] = {}

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            if self._market_conn is not None:
                self._market_conn.close()
            self._conn = None
            self._market_conn = None
            self._market_identity = None
            self._identity = None
            self._cache.clear()

    def _connection(self):
        if not self.path.is_file():
            raise OperationError('database_unavailable', 'ETF数据库尚未建立')
        stat = self.path.stat()
        identity = (str(self.path), stat.st_dev, stat.st_ino)
        if self._identity != identity:
            if self._conn is not None:
                self._conn.close()
            self._conn = database.connect(f'file:{self.path.as_posix()}?mode=ro', uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA query_only=ON')
            self._identity = identity
            self._session = uuid.uuid4().hex
            self._cache.clear()
        return self._conn

    def _market_connection(self):
        if self._snapshot_active:
            return self._pinned_market
        path = resolve_data_path(self.root,'market/equity_daily_raw.sqlite')
        if not path.is_file():
            if self._market_conn is not None:
                self._market_conn.close()
                self._market_conn = None
                self._market_identity = None
            return None
        stat = path.stat()
        identity = (str(path), stat.st_dev, stat.st_ino)
        if identity != self._market_identity:
            if self._market_conn is not None:
                self._market_conn.close()
            self._market_conn = database.connect(f'file:{path.as_posix()}?mode=ro', uri=True, check_same_thread=False)
            self._market_conn.row_factory = sqlite3.Row
            self._market_conn.execute('PRAGMA query_only=ON')
            self._market_identity = identity
            self._session = uuid.uuid4().hex
            self._cache.clear()
        return self._market_conn

    def query(self, params: dict) -> dict:
        with self._lock:
            for attempt in range(3):
                try:
                    return self._query_snapshot(params)
                except _SnapshotChanged:
                    self._cache.clear()
            raise OperationError('snapshot_changed', '数据正在更新，请稍后重试', retryable=True)

    def _query_snapshot(self, params):
        with self._lock:
            conn = self._connection()
            try:
                require_schema(conn)
            except SchemaCompatibilityError as exc:
                raise OperationError(exc.code, 'ETF数据库版本需要匹配的软件或显式升级', details={'version': exc.version}) from exc
            version = conn.execute('PRAGMA data_version').fetchone()[0]
            market = self._market_connection()
            market_version = market.execute('PRAGMA data_version').fetchone()[0] if market else 'none'
            borrowed = lambda **_: _BorrowedConnection(conn)
            calendar = CalendarRepository(resolve_data_path(self.root,'market/equity_daily_raw.sqlite'), borrowed, connect_market=(lambda: _BorrowedConnection(market)) if market is not None else None)
            store = ObserverReadRepository(borrowed, resolve_data_path(self.root,'market/equity_daily_raw.sqlite'), calendar=calendar)
            conn.execute('BEGIN')
            self._pinned_market = market
            self._snapshot_active = True
            try:
                conn.execute('SELECT count(*) FROM sqlite_master').fetchone()
                if market is not None:
                    market.execute('BEGIN')
                    market.execute('SELECT count(*) FROM sqlite_master').fetchone()
                state = store.revision_state() if hasattr(store, 'revision_state') else {}
                business_now = datetime.now(timezone(timedelta(hours=8)))
                clock_bucket = f'{business_now.date().isoformat()}:{business_now.hour >= 16}'
                revision = f"{self._session}:{version}:{market_version}:{state.get('input_revision', 0)}:{state.get('data_revision', 0)}:{clock_bucket}:{METHOD}"
                key = revision + json.dumps({k: v for k, v in params.items() if k != 'if_revision'}, sort_keys=True)
                cached = self._cache.get(key)
                if cached and time.monotonic() - cached[0] < self.ttl:
                    result = copy.deepcopy(cached[1])
                    result['cache_age_seconds'] = round(time.monotonic() - cached[0], 3)
                else:
                    view = params['view']
                    if view == 'health':
                        quote_date = conn.execute('SELECT MAX(trade_date) FROM etf_daily').fetchone()[0]
                        industry_date = conn.execute('SELECT MAX(trade_date) FROM industry_daily').fetchone()[0]
                        expected, calendar_status = self._calendar()
                        calendar_dependency = self._calendar_dependency_status(conn)
                        result = {'status': 'ok', 'database_exists': True, 'revision_state': state, 'business_date': datetime.now(timezone(timedelta(hours=8))).date().isoformat(), 'as_of': quote_date, 'industry_as_of': industry_date, 'expected_trade_date': expected, 'calendar_status': calendar_status, 'calendar_dependency_status': calendar_dependency, 'freshness_status': 'unknown' if not expected else 'stale' if not quote_date or quote_date < expected else 'current', 'quality': {'derived_status': 'STALE' if state.get('dirty_ranges') or calendar_dependency == 'STALE' else 'UNVERIFIED' if calendar_dependency != 'PASS' else 'not_evaluated', 'dirty_group_count': len({r.get('group_id') for r in state.get('dirty_ranges', [])})}}
                    elif view == 'summary':
                        result = self._summary(conn, store, state)
                    elif view == 'detail':
                        result = self._detail(store, params)
                    elif view == 'coverage':
                        result = store.coverage_audit()
                    else:
                        raise OperationError('invalid_params', 'unknown view')
                    if view in {'summary', 'detail'}:
                        dependency = result.get('calendar_dependency_status') or self._calendar_dependency_status(conn)
                        result['calendar_dependency_status'] = dependency
                        if dependency == 'STALE':
                            for row in result.get('items', []) + result.get('series', []):
                                row.update(quality_status='STALE', calendar_status='STALE')
                                row['reason_codes'] = sorted(set(row.get('reason_codes', [])) | {'calendar_dependency_changed'})
                                for field in ('aggregate_amount', 'aggregate_volume', 'relative_amount_20d', 'estimated_net_subscription', 'available_flow_subtotal', 'group_return', 'synthetic_index'):
                                    row[field] = None
                                if 'weekly_strength' in row:
                                    row['weekly_strength']['weekly_volume'] = None
                    if view == 'detail':
                        result = compact_detail(result)
                    result.update(schema_version=SCHEMA, view=view, data_revision=revision, computed_at=_now(), cache_age_seconds=0)
                    if len(self._cache) >= 20:
                        self._cache.clear()
                    self._cache[key] = (time.monotonic(), copy.deepcopy(result))
                if params.get('if_revision') == revision:
                    return {'schema_version': SCHEMA, 'view': params['view'], 'data_revision': revision, 'not_modified': True, 'served_at': _now()}
                result['served_at'] = _now()
                return result
            except sqlite3.Error as exc:
                raise OperationError('database_unavailable', 'ETF数据查询失败', details={'reason': str(exc)}) from exc
            finally:
                conn.rollback()
                if market is not None:
                    market.rollback()
                self._snapshot_active = False
                self._pinned_market = None
                changed = conn.execute('PRAGMA data_version').fetchone()[0] != version or (market is not None and market.execute('PRAGMA data_version').fetchone()[0] != market_version)
                for path, identity in ((self.path, self._identity), (resolve_data_path(self.root,'market/equity_daily_raw.sqlite'), self._market_identity)):
                    if identity is not None:
                        try:
                            stat = path.stat()
                            changed = changed or (str(path), stat.st_dev, stat.st_ino) != identity
                        except FileNotFoundError:
                            changed = True
                    elif path.is_file():
                        changed = True
                if changed:
                    raise _SnapshotChanged()

    def _calendar_dependency_status(self, conn):
        """Compare the managed input fingerprint without registering or dirtying it."""
        previous = conn.execute("SELECT value FROM schema_meta WHERE key='observer_calendar_digest'").fetchone()
        if not previous:
            return 'UNVERIFIED'
        source = self._market_connection()
        payload = None
        if source is not None and source.execute("SELECT 1 FROM sqlite_master WHERE name='trade_calendar'").fetchone():
            payload = {'source': str(resolve_data_path(self.root,'market/equity_daily_raw.sqlite')), 'rows': [tuple(row) for row in source.execute("SELECT cal_date,is_open FROM trade_calendar WHERE exchange='SSE' ORDER BY cal_date")]}
        if payload is None and conn.execute("SELECT 1 FROM sqlite_master WHERE name='etf_trade_calendar'").fetchone():
            payload = {'source': 'managed_etf_trade_calendar', 'rows': [tuple(row) for row in conn.execute('SELECT trade_date FROM etf_trade_calendar ORDER BY trade_date')]}
        if payload is None:
            return 'UNAVAILABLE'
        return 'PASS' if previous[0] == digest(payload) else 'STALE'

    def _calendar(self):
        now = datetime.now(timezone(timedelta(hours=8)))
        cutoff = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
        conn = self._market_connection()
        if conn is None:
            try:
                horizon = self._conn.execute('SELECT MAX(trade_date) FROM etf_trade_calendar').fetchone()[0]
                if not horizon or horizon < cutoff.isoformat():
                    return (None, 'calendar_outdated')
                value = self._conn.execute('SELECT MAX(trade_date) FROM etf_trade_calendar WHERE trade_date<=?', (cutoff.isoformat(),)).fetchone()[0]
                return (value, 'available' if value else 'calendar_unavailable')
            except sqlite3.Error:
                return (None, 'calendar_unavailable')
        try:
            horizon = conn.execute("SELECT MAX(replace(cal_date,'-','')) FROM trade_calendar WHERE exchange='SSE'").fetchone()[0]
            compact_cutoff = cutoff.strftime('%Y%m%d')
            if not horizon or str(horizon) < compact_cutoff:
                return (None, 'calendar_outdated')
            value = conn.execute("SELECT MAX(replace(cal_date,'-','')) FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND replace(cal_date,'-','')<=?", (compact_cutoff,)).fetchone()[0]
            value = datetime.strptime(str(value), '%Y%m%d').date().isoformat() if value else None
            return (value, 'available' if value else 'calendar_unavailable')
        except sqlite3.Error:
            return (None, 'calendar_unavailable')

    def _holding_returns(self, holdings):
        conn = self._market_connection()
        if conn is None:
            return 'source_unavailable'
        codes = sorted({str(row['stock_code']).zfill(6) for row in holdings if row.get('stock_code')})
        if not codes:
            return 'no_holdings'
        symbols = [f'{code}.{market}' for code in codes for market in ('SH', 'SZ', 'BJ')]
        try:
            rows = conn.execute(f"WITH codes(ts_code) AS (VALUES {','.join(('(?)' for _ in symbols))})\n                SELECT d.ts_code,d.trade_date,d.close,d.pre_close,d.pct_chg FROM codes c JOIN equity_daily_raw d\n                ON d.ts_code=c.ts_code AND d.trade_date=(SELECT MAX(x.trade_date) FROM equity_daily_raw x WHERE x.ts_code=c.ts_code)", symbols)
            by_code = {}
            for row in rows:
                code = row['ts_code'].split('.')[0]
                value = row['pct_chg'] / 100 if row['pct_chg'] is not None else row['close'] / row['pre_close'] - 1 if row['close'] is not None and row['pre_close'] else None
                if code not in by_code or row['trade_date'] > by_code[code]['return_trade_date']:
                    by_code[code] = {'daily_return': value, 'return_trade_date': row['trade_date']}
            for holding in holdings:
                holding.update(by_code.get(str(holding['stock_code']).zfill(6), {'daily_return': None, 'return_trade_date': None}))
            return 'available' if len(by_code) == len(codes) else 'partial'
        except sqlite3.Error:
            return 'source_unavailable'

    def _summary(self, conn, store, revision_state):
        groups = [dict(row) for row in conn.execute('WITH latest AS (\n            SELECT group_id,MAX(trade_date) day FROM industry_daily GROUP BY group_id)\n            SELECT g.group_id,g.group_name,g.group_type,g.display_order,i.*\n            FROM industry_groups g LEFT JOIN latest x ON x.group_id=g.group_id\n            LEFT JOIN industry_daily i ON i.group_id=g.group_id AND i.trade_date=x.day\n            WHERE g.is_active=1 ORDER BY g.display_order,g.group_id')]
        if hasattr(store, 'apply_revision_quality'):
            groups = store.apply_revision_quality(groups, revision_state)
        windows = [(r['group_id'], week_start(r['trade_date']), r['trade_date']) for r in groups if r.get('trade_date')]
        week_by_group = defaultdict(list)
        if windows:
            week_rows = [dict(r) for r in conn.execute(f"WITH windows(group_id,start,end) AS (VALUES {','.join(('(?,?,?)' for _ in windows))})\n                SELECT i.* FROM windows w JOIN industry_daily i ON i.group_id=w.group_id AND i.trade_date BETWEEN w.start AND w.end", [v for row in windows for v in row])]
            if hasattr(store, 'apply_revision_quality'):
                week_rows = store.apply_revision_quality(week_rows, revision_state)
            for row in week_rows:
                week_by_group[row['group_id']].append(row)
        leaders = {r['group_id']: dict(r) for r in conn.execute('WITH latest AS (\n            SELECT g.group_id,substr(COALESCE(MAX(i.trade_date),(SELECT MAX(trade_date) FROM etf_daily)),1,7) month\n            FROM industry_groups g LEFT JOIN industry_daily i ON i.group_id=g.group_id GROUP BY g.group_id)\n            SELECT l.*,m.fund_name,m.tracking_index_name FROM monthly_leaders l\n            JOIN latest x ON x.group_id=l.group_id AND x.month=l.leader_month\n            JOIN etf_master m ON m.etf_code=l.etf_code WHERE l.is_current=1')}
        from guanlan_data.repositories.guanlan_backend.market_etf.observer_read_repository import mapped_quote_leader
        for row in groups:
            if row['group_id'] not in leaders:
                candidate=mapped_quote_leader(conn,row['group_id'])
                if candidate:leaders[row['group_id']]=candidate
        focus = [dict(r) for r in conn.execute('SELECT f.*,m.fund_name,m.tracking_index_name FROM etf_focus_watchlist f\n            JOIN etf_master m ON m.etf_code=f.etf_code WHERE f.is_active=1 ORDER BY f.display_order,f.etf_code')]
        codes = sorted({r['etf_code'] for r in leaders.values()} | {r['etf_code'] for r in focus})
        bars = defaultdict(list)
        holdings = defaultdict(list)
        if codes:
            placeholders = ','.join(('?' for _ in codes))
            for row in conn.execute(f'WITH ranked AS (SELECT etf_code,trade_date,open,high,low,close,prev_close,volume,amount,\n                ROW_NUMBER() OVER(PARTITION BY etf_code ORDER BY trade_date DESC) n FROM etf_daily WHERE etf_code IN ({placeholders}))\n                SELECT * FROM ranked WHERE n<=400 ORDER BY etf_code,trade_date', codes):
                bars[row['etf_code']].append(dict(row))
            for row in conn.execute(f'SELECT h.etf_code,h.report_date,h.rank,h.stock_code,h.stock_name,h.weight_pct\n                FROM etf_holdings h WHERE h.etf_code IN ({placeholders}) AND h.rank<=3\n                AND h.report_date=(SELECT MAX(x.report_date) FROM etf_holdings x WHERE x.etf_code=h.etf_code)\n                ORDER BY h.etf_code,h.rank', codes):
                holdings[row['etf_code']].append(dict(row))
        expected, calendar_status = self._calendar()
        holding_status = self._holding_returns([holding for rows in holdings.values() for holding in rows])

        def enrich(row, code):
            raw = bars.get(code, [])
            adjusted, _ = continuous_adjust_bars(raw)
            signal = weekly_strength(adjusted)
            last = raw[-1] if raw else {}
            previous = last.get('prev_close') or (raw[-2].get('close') if len(raw) > 1 else None)
            daily_return = last['close'] / previous - 1 if last.get('close') is not None and previous else None
            row.update(weekly_strength=signal, top_holdings=holdings.get(code, []), price_date=last.get('trade_date'), daily_return=daily_return, leader_return=daily_return, latest_close=last.get('close'), stock_returns_status=holding_status, stale_reason='quote_lag' if expected and (not last or last['trade_date'] < expected) else None)
        for row in groups:
            leader = leaders.get(row['group_id'], {})
            code = leader.get('etf_code')
            row.update(target={'kind': 'group', 'id': row['group_id']}, leader_etf_code=code, leader_name=leader.get('fund_name'), tracking_index_name=leader.get('tracking_index_name'), industry_date=row.get('trade_date'))
            enrich(row, code)
            if row.get('trade_date'):
                week = week_by_group[row['group_id']]
                row['weekly_strength']['weekly_volume'] = sum((r['aggregate_volume'] for r in week)) if week and all((r.get('aggregate_volume') is not None for r in week)) else None
            row['selection_kind']=leader.get('selection_kind','monthly_leader') if code else None
            row['selection_reason']=leader.get('selection_reason')
            row['volume_scope'] = 'industry_members'
        for row in focus:
            row.update(target={'kind': 'etf', 'id': row['etf_code']}, group_type='focus')
            enrich(row, row['etf_code'])
            row.update(quality_status='AVAILABLE' if row['price_date'] else 'MISSING', volume_scope='current_etf')
        days = [r.get('price_date') for r in groups + focus if r.get('price_date')]
        return {'as_of': max(days) if days else None, 'expected_trade_date': expected, 'calendar_status': calendar_status, 'calendar_dependency_status': self._calendar_dependency_status(conn), 'revision_state': revision_state, 'items': groups, 'focus_items': focus}

    def _detail(self, store, params):
        target = params['target']
        kwargs = {'limit': params['limit'], 'include_warmup': True, 'skip_stock_returns': True}
        method = store.group_detail if target['kind'] == 'group' else store.focus_detail
        result = method(target['id'], **kwargs)
        if result is None:
            raise OperationError('target_not_found', '观察对象不存在或未启用')
        for field in ('leader_bars', 'leader_bars_raw'):
            result[field] = [{k: v for k, v in row.items() if k in BAR_FIELDS} for row in result[field]]
        result.update(target=target, price_mode=params['price_mode'], period=params['period'], indicator_method_version='ma30w_asof_day_v1', stock_returns_status=self._holding_returns(result.get('holdings', [])))
        raw = result['leader_bars_raw']
        result['price_date'] = raw[-1]['trade_date'] if raw else None
        result['weekly_strength'] = weekly_strength(result['leader_bars'])
        selected = result['leader_bars_raw' if params['price_mode'] == 'raw' else 'leader_bars']
        result['chart_bars'] = chart_rows(selected, params['period'])
        result.pop('leader_bars')
        result.pop('leader_bars_raw')
        return result

class _SnapshotChanged(Exception):
    pass
