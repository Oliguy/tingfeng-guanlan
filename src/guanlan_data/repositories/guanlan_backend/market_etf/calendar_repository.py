"""Read-only managed SSE calendar adapter, with content-bound snapshots."""
from __future__ import annotations
from guanlan_data import sqlite as database
import sqlite3
from guanlan_domain.guanlan_backend.market_etf.domain.primitives import digest; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import normalize_date
from guanlan_domain.guanlan_backend.market_etf.ports import CalendarSnapshot

class CalendarRepository:

    def __init__(self, equity_path, connect_etf, *, connect_market=None):
        self._path_provider = equity_path if callable(equity_path) else lambda: equity_path
        self._connect_etf = connect_etf
        self._connect_market = connect_market

    @property
    def _equity_path(self):
        return self._path_provider()

    def _read(self, source):
        if source == 'managed_etf_trade_calendar':
            conn = self._connect_etf(readonly=True)
            query = 'SELECT trade_date,1 FROM etf_trade_calendar ORDER BY trade_date'
            table = 'etf_trade_calendar'
        else:
            if not self._equity_path.is_file():
                return None
            conn = self._connect_market() if self._connect_market else database.connect(self._equity_path.as_uri() + '?mode=ro', uri=True)
            query = "SELECT cal_date,is_open FROM trade_calendar WHERE exchange='SSE' ORDER BY cal_date"
            table = 'trade_calendar'
        try:
            if not conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                return None
            original = [tuple(row) for row in conn.execute(query)]
        finally:
            conn.close()
        payload = {'source': source, 'rows': [(r[0],) for r in original] if source == 'managed_etf_trade_calendar' else original}
        rows = tuple(((normalize_date(str(day)), int(opened)) for day, opened in original))
        days = [day for day, _ in rows]
        return CalendarSnapshot(source, digest(payload), rows, min(days) if days else None, max(days) if days else None)

    def snapshot(self, start=None, end=None, *, require_coverage=False):
        candidates = (str(self._equity_path), 'managed_etf_trade_calendar')
        fallback = None
        for source in candidates:
            value = self._read(source)
            if value is None:
                continue
            if start is None:
                return value
            fallback = value
            if require_coverage:
                try:
                    value.dates(start, end, require_coverage=True)
                    return value
                except RuntimeError:
                    continue
            elif value.dates(start, end):
                return value
        if require_coverage:
            raise RuntimeError('calendar_coverage_unverified')
        return fallback or CalendarSnapshot('missing', digest({'source': 'missing'}), (), None, None)

    def verify(self, snapshot):
        current = self._read(snapshot.source) if snapshot.source != 'missing' else self.snapshot()
        if current is None or current.version != snapshot.version:
            raise RuntimeError('observer_calendar_changed_during_recompute')
