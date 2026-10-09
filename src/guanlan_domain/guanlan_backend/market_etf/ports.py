"""ETF boundary contracts. Ports expose values, never SQLite connections."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

class SourceError(RuntimeError):
    """Source boundary failure, independent of any concrete transport."""

@dataclass(frozen=True)
class CalendarSnapshot:
    source: str
    version: str
    rows: tuple[tuple[str, int], ...]
    covered_start: str | None
    covered_end: str | None

    def dates(self, start: str, end: str, *, require_coverage: bool=False) -> list[str]:
        if require_coverage and (not (self.covered_start and self.covered_end and (self.covered_start <= start <= end <= self.covered_end))):
            raise RuntimeError('calendar_coverage_unverified')
        return sorted({day for day, opened in self.rows if opened and start <= day <= end})

class CalendarPort(Protocol):

    def snapshot(self, start: str | None=None, end: str | None=None, *, require_coverage: bool=False) -> CalendarSnapshot:
        ...

    def verify(self, snapshot: CalendarSnapshot) -> None:
        ...

class HistoryReadPort(Protocol):

    def latest_history(self, code: str, end: str) -> str | None:
        ...

    def present_dates(self, code: str, start: str, end: str) -> set[str]:
        ...

    def history_start(self, code: str, fallback: str) -> str:
        ...

    def target_codes(self, kind: str, identifier: str, start: str, end: str) -> list[str]:
        ...

    def active_group_ids(self) -> list[str]:
        ...

    def watched_codes(self) -> list[str]:
        ...

class SourcePort(Protocol):

    def available(self) -> bool:
        ...

    def call_batch(self, requests: Sequence[Mapping[str, Any]], *, cancelled: Callable[[], bool] | None=None) -> list[dict[str, Any]]:
        ...

    def is_trade_date(self, day: str) -> bool:
        ...

    def fetch_universe(self) -> list[dict[str, Any]]:
        ...

class ClassificationPort(Protocol):

    def quality(self) -> dict[str, Any]:
        ...

    def classify(self, profile: Mapping[str, Any], holdings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        ...

class ObserverRepositoryPort(HistoryReadPort, Protocol):
    """Compatibility facade used by orchestration; implementations compose repositories."""

    def initialize(self) -> dict[str, Any]:
        ...

    def require_ready(self, *, writable: bool=True) -> int:
        ...

    def start_run(self, run_type: str, parameters: Mapping[str, Any]) -> str:
        ...

    def start_child_run(self, parent_run_id: str, run_type: str, parameters: Mapping[str, Any]) -> str:
        ...

    def finish_run(self, run_id: str, status: str, **details: Any) -> None:
        ...

    def master_rows(self, *, state: str | None=None) -> list[dict[str, Any]]:
        ...

    def expected_trade_dates(self, start: str, end: str) -> list[str]:
        ...

    def active_member_codes(self, group_id: str) -> list[str]:
        ...

    def latest_holdings_by_code(self, codes: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        ...

    def upsert_master_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        ...

    def upsert_daily_rows(self, day: str, rows: Sequence[Mapping[str, Any]]) -> int:
        ...

    def update_profile(self, profile: Mapping[str, Any]) -> None:
        ...

    def upsert_holdings(self, rows: Sequence[Mapping[str, Any]]) -> int:
        ...

    def upsert_constituents(self, rows: Sequence[Mapping[str, Any]]) -> int:
        ...

    def apply_share_history(self, rows: Sequence[Mapping[str, Any]]) -> int:
        ...

    def ensure_group(self, group_id: str, name: str, group_type: str, taxonomy_node_id: str | None=None) -> None:
        ...

    def set_classification(self, code: str, **values: Any) -> None:
        ...

    def deactivate_empty_groups(self) -> int:
        ...

    def select_monthly_leader(self, month: str, group_id: str, *, force: bool=False) -> dict[str, Any]:
        ...

    def recompute_group_daily(self, group_id: str, start: str, end: str) -> int:
        ...

    def recompute_dirty(self, end: str | None=None) -> dict[str, Any]:
        ...

    def validate_group(self, group_id: str, start: str, end: str) -> dict[str, Any]:
        ...

    def record_quality_check(self, run_id: str, name: str, status: str, details: Mapping[str, Any]) -> None:
        ...
