from __future__ import annotations
from guanlan_data import sqlite as database

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd


SCHEMA_VERSION = "equity_status_daily_v2"
DIRECT_ST_COVERAGE_START = date(2017, 1, 1)
_RISK_NAME = re.compile(r"(?:\*?ST|S\*ST|SST|PT)", re.IGNORECASE)
_SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SZ_PREFIXES = ("000", "001", "002", "003", "300", "301")
Progress = Callable[[str, float], None]


def initialize_equity_status_database(path: str | Path) -> None:
    """Initialize the point-in-time status store for an authorized importer."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = database.connect(target, timeout=60)
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS equity_status_daily (
                exchange TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                is_st INTEGER NOT NULL CHECK(is_st IN (0,1)),
                is_delisted INTEGER NOT NULL CHECK(is_delisted IN (0,1)),
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(exchange, stock_code, trade_date)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS equity_master_snapshot (
                ts_code TEXT PRIMARY KEY,
                stock_code TEXT NOT NULL,
                exchange TEXT NOT NULL,
                name TEXT NOT NULL,
                list_status TEXT NOT NULL,
                list_date TEXT NOT NULL,
                delist_date TEXT,
                fetched_at TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS namechange_source (
                ts_code TEXT NOT NULL,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                ann_date TEXT,
                change_reason TEXT,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(ts_code, start_date, name)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS stock_st_source (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT,
                type_name TEXT,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY(ts_code, trade_date)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS trade_calendar_snapshot (
                trade_date TEXT PRIMARY KEY,
                fetched_at TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS source_runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL,
                counts_json TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                source_hashes_json TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS idx_equity_status_date
                ON equity_status_daily(trade_date, exchange, stock_code);
            CREATE INDEX IF NOT EXISTS idx_stock_st_source_date
                ON stock_st_source(trade_date, ts_code);
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
    finally:
        conn.close()


def import_equity_status_rows(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    """Idempotently import source-attributed rows; callers own authorization."""

    payload = []
    minimum: str | None = None
    maximum: str | None = None
    for row in rows:
        exchange = str(row.get("exchange") or "").upper()
        stock_code = str(row.get("stock_code") or "")
        trade_date = _iso_date(row.get("trade_date"))
        source = str(row.get("source") or "")
        source_as_of = str(row.get("source_as_of") or "")
        if (
            exchange not in {"SH", "SZ"}
            or len(stock_code) != 6
            or trade_date is None
            or not source
            or not source_as_of
        ):
            raise ValueError("invalid point-in-time equity status row")
        minimum = trade_date if minimum is None else min(minimum, trade_date)
        maximum = trade_date if maximum is None else max(maximum, trade_date)
        payload.append(
            (
                exchange,
                stock_code,
                trade_date,
                int(bool(row.get("is_st"))),
                int(bool(row.get("is_delisted"))),
                source,
                source_as_of,
            )
        )
    initialize_equity_status_database(path)
    conn = database.connect(Path(path), timeout=60)
    try:
        conn.executemany(
            """INSERT INTO equity_status_daily(
                   exchange,stock_code,trade_date,is_st,is_delisted,source,source_as_of
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(exchange,stock_code,trade_date) DO UPDATE SET
                 is_st=excluded.is_st,
                 is_delisted=excluded.is_delisted,
                 source=excluded.source,
                 source_as_of=excluded.source_as_of""",
            payload,
        )
        if minimum and maximum:
            conn.executemany(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                (("coverage_start", minimum), ("coverage_end", maximum), ("quality_status", "PASS")),
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def refresh_equity_status_daily(
    client: Any,
    target_path: str | Path,
    *,
    start_date: str,
    end_date: str,
    raw_equity_db: str | Path | None = None,
    request_interval_ms: int = 80,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Build and atomically publish the historical ST/delist daily database.

    ``stock_st`` is preferred from 2017 onward. If that optional endpoint is
    unavailable, the whole requested range is derived only from complete
    historical ``namechange`` effective intervals. Current names are never
    backfilled into an uncovered historical interval.
    """

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end or end > date.today():
        raise ValueError("equity status range must be ordered and not in the future")
    if not 0 <= int(request_interval_ms) <= 5_000:
        raise ValueError("request_interval_ms must be between 0 and 5000")
    notify = progress or (lambda _label, _fraction: None)
    target = Path(target_path).resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + f".staging-{uuid.uuid4().hex}")
    if staging.exists():
        raise FileExistsError("equity status staging path already exists")
    run_id = "equity-status-" + uuid.uuid4().hex
    started_at = _now()
    fetched_at = started_at
    wait = max(0, int(request_interval_ms)) / 1000.0

    notify("读取股票主数据", 0.02)
    master_rows = _fetch_stock_basic(client, wait)
    notify("读取交易日历", 0.08)
    calendar_rows = _fetch_trade_calendar(client, start, end, wait)
    notify("读取历史名称区间", 0.14)
    name_rows = _fetch_paginated(
        client,
        "namechange",
        fields="ts_code,name,start_date,end_date,ann_date,change_reason",
        page_size=10_000,
        wait=wait,
    )
    direct_start = max(start, DIRECT_ST_COVERAGE_START)
    st_rows: list[dict[str, Any]] = []
    st_source_mode = "stock_st_daily"
    if direct_start <= end:
        windows = list(_month_windows(direct_start, end))
        try:
            for index, (left, right) in enumerate(windows):
                st_rows.extend(
                    _fetch_paginated(
                        client,
                        "stock_st",
                        fields="ts_code,name,trade_date,type,type_name",
                        page_size=1_000,
                        wait=wait,
                        start_date=_compact(left),
                        end_date=_compact(right),
                    )
                )
                notify(
                    f"读取逐日 ST 名单 {left:%Y-%m}",
                    0.18 + 0.25 * (index + 1) / max(1, len(windows)),
                )
        except RuntimeError:
            # ``stock_st`` is a high-credit optional Tushare endpoint. The
            # lower-credit namechange endpoint still provides point-in-time
            # effective intervals. It is accepted only when every listed
            # trading day is covered by exactly one interval; otherwise the
            # existing strict missing-point-in-time gate blocks publication.
            st_rows.clear()
            st_source_mode = "namechange_effective_interval_fallback"
            notify("逐日 ST 接口不可用，校验完整历史名称区间", 0.43)

    normalized_master = _normalize_master(master_rows, fetched_at)
    open_values = {
        _iso_date(row.get("cal_date"))
        for row in calendar_rows
        if int(row.get("is_open") or 0) == 1
    }
    open_dates = sorted(
        value for value in open_values if value and start_date <= value <= end_date
    )
    normalized_names = _normalize_namechanges(name_rows, fetched_at)
    normalized_st = _normalize_stock_st(st_rows, fetched_at)
    source_hashes = {
        "stock_basic": _rows_hash(normalized_master),
        "trade_cal": _rows_hash([{"trade_date": value} for value in open_dates]),
        "namechange": _rows_hash(normalized_names),
        "stock_st": _rows_hash(normalized_st),
    }
    quality = _source_quality(
        normalized_master,
        open_dates,
        normalized_names,
        normalized_st,
        start,
        end,
        st_source_mode=st_source_mode,
    )
    if quality["status"] != "PASS":
        return {
            "status": "BLOCKED",
            "code": "blocked_point_in_time_status_source_quality",
            "target": str(target),
            "mutation_performed": False,
            "quality": quality,
            "counts": _counts(normalized_master, open_dates, normalized_names, normalized_st, 0),
        }

    initialize_equity_status_database(staging)
    conn = database.connect(staging, timeout=60, uri=True)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executemany(
            """INSERT INTO equity_master_snapshot(
                   ts_code,stock_code,exchange,name,list_status,list_date,delist_date,fetched_at
               ) VALUES(:ts_code,:stock_code,:exchange,:name,:list_status,:list_date,:delist_date,:fetched_at)""",
            normalized_master,
        )
        conn.executemany(
            """INSERT INTO namechange_source(
                   ts_code,name,start_date,end_date,ann_date,change_reason,fetched_at
               ) VALUES(:ts_code,:name,:start_date,:end_date,:ann_date,:change_reason,:fetched_at)""",
            normalized_names,
        )
        conn.executemany(
            """INSERT INTO stock_st_source(
                   ts_code,trade_date,name,type,type_name,fetched_at
               ) VALUES(:ts_code,:trade_date,:name,:type,:type_name,:fetched_at)""",
            normalized_st,
        )
        conn.executemany(
            "INSERT INTO trade_calendar_snapshot(trade_date,fetched_at) VALUES(?,?)",
            ((value, fetched_at) for value in open_dates),
        )
        conn.commit()

        notify("展开逐日点时状态", 0.48)
        status_count, unresolved = _write_daily_rows(
            conn,
            normalized_master,
            open_dates,
            normalized_names,
            normalized_st,
            end,
            st_source_mode=st_source_mode,
            progress=notify,
        )
        reconciled_count = 0
        if raw_equity_db is not None:
            notify("对账正式行情覆盖", 0.91)
            reconciled_count, reconcile_unresolved = _reconcile_market_bar_coverage(
                conn,
                raw_equity_db=Path(raw_equity_db),
                start_date=start_date,
                end_date=end_date,
                master=normalized_master,
                names=normalized_names,
                st_rows=normalized_st,
                st_source_mode=st_source_mode,
                source_as_of=end_date,
            )
            unresolved.extend(reconcile_unresolved)
        quality["market_bar_coverage_checked"] = raw_equity_db is not None
        quality["market_bar_reconciled_rows"] = reconciled_count
        quality["unresolved_historical_days"] = len(unresolved)
        quality["unresolved_sample"] = unresolved[:20]
        if unresolved:
            quality["status"] = "BLOCKED"
            quality["reason"] = "historical name events contain conflicting risk intervals"
            raise _PointInTimeCoverageError(quality)

        counts = _counts(
            normalized_master,
            open_dates,
            normalized_names,
            normalized_st,
            status_count + reconciled_count,
        )
        counts["market_bar_reconciled_rows"] = reconciled_count
        finished_at = _now()
        conn.executemany(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
            (
                ("schema_version", SCHEMA_VERSION),
                ("coverage_start", start_date),
                ("coverage_end", end_date),
                ("direct_stock_st_start", DIRECT_ST_COVERAGE_START.isoformat()),
                ("st_source_mode", st_source_mode),
                ("quality_status", "PASS"),
                ("source_as_of", end_date),
                ("source_hashes_json", json.dumps(source_hashes, sort_keys=True)),
                ("completed_at", finished_at),
            ),
        )
        conn.execute(
            """INSERT INTO source_runs(
                   run_id,started_at,finished_at,start_date,end_date,status,
                   counts_json,quality_json,source_hashes_json
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                started_at,
                finished_at,
                start_date,
                end_date,
                "PASS",
                json.dumps(counts, ensure_ascii=False, sort_keys=True),
                json.dumps(quality, ensure_ascii=False, sort_keys=True),
                json.dumps(source_hashes, sort_keys=True),
            ),
        )
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError("equity status staging database failed integrity_check")
    except _PointInTimeCoverageError as exc:
        conn.close()
        _remove_staging_database(staging)
        return {
            "status": "BLOCKED",
            "code": "blocked_missing_point_in_time_st",
            "target": str(target),
            "mutation_performed": False,
            "quality": exc.quality,
            "counts": _counts(normalized_master, open_dates, normalized_names, normalized_st, 0),
        }
    except Exception:
        conn.close()
        _remove_staging_database(staging)
        raise
    else:
        conn.close()

    snapshot: str | None = None
    if target.exists():
        snapshot_root = target.parent / "snapshots"
        snapshot_root.mkdir(parents=True, exist_ok=True)
        digest = _sha256_file(target)[:12]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = snapshot_root / f"{target.stem}.{stamp}.{digest}{target.suffix}"
        if backup.exists():
            _remove_staging_database(staging)
            raise FileExistsError("equity status snapshot target already exists")
        os.replace(target, backup)
        snapshot = str(backup)
    try:
        os.replace(staging, target)
    except Exception:
        if snapshot and not target.exists():
            os.replace(Path(snapshot), target)
        raise
    notify("逐日状态库已原子发布", 1.0)
    return {
        "status": "PASS",
        "code": "equity_status_ready",
        "target": str(target),
        "snapshot": snapshot,
        "mutation_performed": True,
        "coverage_start": start_date,
        "coverage_end": end_date,
        "counts": counts,
        "quality": quality,
        "source_hashes": source_hashes,
        "database_sha256": _sha256_file(target),
    }


def equity_status_status(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve(strict=False)
    if not target.is_file():
        return {
            "status": "BLOCKED",
            "code": "blocked_missing_point_in_time_st",
            "path": str(target),
        }
    conn = database.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(conn.execute("SELECT key,value FROM metadata").fetchall())
        row_count = int(conn.execute("SELECT COUNT(*) FROM equity_status_daily").fetchone()[0])
        st_count = int(conn.execute("SELECT COUNT(*) FROM equity_status_daily WHERE is_st=1").fetchone()[0])
        delisted_count = int(conn.execute("SELECT COUNT(*) FROM equity_status_daily WHERE is_delisted=1").fetchone()[0])
        quality_row = conn.execute(
            """SELECT quality_json FROM source_runs
               WHERE status='PASS' ORDER BY finished_at DESC LIMIT 1"""
        ).fetchone()
        latest_quality = json.loads(str(quality_row[0])) if quality_row else {}
    except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError):
        return {
            "status": "BLOCKED",
            "code": "blocked_missing_point_in_time_st",
            "path": str(target),
        }
    finally:
        conn.close()
    valid = (
        metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("quality_status") == "PASS"
        and bool(metadata.get("coverage_start"))
        and bool(metadata.get("coverage_end"))
        and row_count > 0
    )
    bar_coverage_verified = bool(
        valid
        and latest_quality
        and int(latest_quality.get("unresolved_historical_days", -1)) == 0
        and (
            latest_quality.get("market_bar_coverage_checked") is True
            or int(latest_quality.get("market_bar_reconciled_rows", 0)) > 0
        )
    )
    return {
        "status": "PASS" if valid else "BLOCKED",
        "code": "ready" if valid else "blocked_missing_point_in_time_st",
        "path": str(target),
        "coverage_start": metadata.get("coverage_start"),
        "coverage_end": metadata.get("coverage_end"),
        "source_as_of": metadata.get("source_as_of"),
        "rows": row_count,
        "st_rows": st_count,
        "delisted_rows": delisted_count,
        "bar_coverage_verified": bar_coverage_verified,
        "latest_quality": latest_quality,
        "database_sha256": _sha256_file(target),
    }


def load_equity_status_readonly(
    path: str | Path,
    start_date: str,
    end_date: str,
    *,
    risk_only: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    target = Path(path).resolve()
    status = equity_status_status(target)
    if status["status"] != "PASS":
        return pd.DataFrame(), {
            **status,
            "detail": "逐日 ST/退市状态库不存在、未通过质量门禁或结构不受支持",
        }
    if str(status["coverage_start"]) > start_date or str(status["coverage_end"]) < end_date:
        return pd.DataFrame(), {
            **status,
            "status": "BLOCKED",
            "code": "blocked_missing_point_in_time_st",
            "detail": (
                f"逐日状态覆盖 {status['coverage_start']} 至 {status['coverage_end']}，"
                f"不能完整覆盖研究区间 {start_date} 至 {end_date}"
            ),
        }
    if risk_only and not status.get("bar_coverage_verified"):
        return pd.DataFrame(), {
            **status,
            "status": "BLOCKED",
            "code": "blocked_missing_point_in_time_st",
            "detail": "逐日状态库缺少正式行情逐键对账通过的质量证据，不能使用稀疏风险行模式",
        }
    before = target.stat()
    conn = database.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        risk_clause = " AND (is_st=1 OR is_delisted=1)" if risk_only else ""
        frame = pd.read_sql_query(
            """SELECT exchange,stock_code,trade_date,is_st,is_delisted,source,source_as_of
               FROM equity_status_daily WHERE trade_date BETWEEN ? AND ?"""
            + risk_clause,
            conn,
            params=(start_date, end_date),
        )
    finally:
        conn.close()
    after = target.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("point-in-time equity status database changed during read")
    if frame.empty and not risk_only:
        return frame, {
            **status,
            "status": "BLOCKED",
            "code": "blocked_missing_point_in_time_st",
            "detail": "研究区间没有逐日状态记录",
        }
    return frame, {
        **status,
        "status": "PASS",
        "code": "ready",
        "loaded_rows": int(len(frame)),
        "loading_mode": (
            "point_in_time_risk_rows_v1" if risk_only else "point_in_time_full_rows_v1"
        ),
        "authoritative_ordinary_default": bool(risk_only),
        "size_bytes": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "detail": (
            "逐日点时状态覆盖完整；正式行情逐键对账通过，仅加载 ST/退市风险行"
            if risk_only
            else "逐日点时状态覆盖完整"
        ),
    }


class _PointInTimeCoverageError(RuntimeError):
    def __init__(self, quality: dict[str, Any]) -> None:
        super().__init__(str(quality.get("reason") or "point-in-time coverage is incomplete"))
        self.quality = quality


def _fetch_stock_basic(client: Any, wait: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fields = "ts_code,symbol,name,exchange,list_status,list_date,delist_date"
    for status in ("L", "P", "D"):
        rows.extend(_call(client, "stock_basic", exchange="", list_status=status, fields=fields))
        _wait(wait)
    return rows


def _fetch_trade_calendar(client: Any, start: date, end: date, wait: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in range(start.year, end.year + 1):
        left = max(start, date(year, 1, 1))
        right = min(end, date(year, 12, 31))
        rows.extend(
            _call(
                client,
                "trade_cal",
                exchange="SSE",
                start_date=_compact(left),
                end_date=_compact(right),
                fields="exchange,cal_date,is_open,pretrade_date",
            )
        )
        _wait(wait)
    return rows


def _fetch_paginated(
    client: Any,
    endpoint: str,
    *,
    fields: str,
    page_size: int,
    wait: float,
    **params: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    previous_hash: str | None = None
    while True:
        page = _call(
            client,
            endpoint,
            fields=fields,
            limit=page_size,
            offset=offset,
            **params,
        )
        digest = _rows_hash(page)
        if page and previous_hash == digest:
            raise RuntimeError(f"Tushare {endpoint} pagination did not advance")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        previous_hash = digest
        offset += page_size
        _wait(wait)


def _call(client: Any, endpoint: str, **params: Any) -> list[dict[str, Any]]:
    last: Exception | None = None
    for attempt in range(3):
        try:
            frame = getattr(client, endpoint)(**params)
            if frame is None:
                return []
            if hasattr(frame, "to_dict"):
                values = frame.to_dict(orient="records")
            elif isinstance(frame, list):
                values = frame
            else:
                raise TypeError(f"Tushare {endpoint} returned an unsupported payload")
            return [_clean_row(row) for row in values]
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Tushare endpoint failed: {endpoint}") from last


def _normalize_master(rows: list[dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        ts_code = str(row.get("ts_code") or "").upper()
        if not _ordinary_a_share(ts_code):
            continue
        list_date = _iso_date(row.get("list_date"))
        if not list_date:
            continue
        exchange = ts_code.rsplit(".", 1)[-1]
        deduped[ts_code] = {
            "ts_code": ts_code,
            "stock_code": ts_code[:6],
            "exchange": exchange,
            "name": str(row.get("name") or ""),
            "list_status": str(row.get("list_status") or ""),
            "list_date": list_date,
            "delist_date": _iso_date(row.get("delist_date")),
            "fetched_at": fetched_at,
        }
    return [deduped[key] for key in sorted(deduped)]


def _normalize_namechanges(rows: list[dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        ts_code = str(row.get("ts_code") or "").upper()
        start = _iso_date(row.get("start_date"))
        name = str(row.get("name") or "").strip()
        if not _ordinary_a_share(ts_code) or not start or not name:
            continue
        item = {
            "ts_code": ts_code,
            "name": name,
            "start_date": start,
            "end_date": _iso_date(row.get("end_date")),
            "ann_date": _iso_date(row.get("ann_date")),
            "change_reason": str(row.get("change_reason") or "") or None,
            "fetched_at": fetched_at,
        }
        deduped[(ts_code, start, name)] = item
    return [deduped[key] for key in sorted(deduped)]


def _normalize_stock_st(rows: list[dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        ts_code = str(row.get("ts_code") or "").upper()
        trade_date = _iso_date(row.get("trade_date"))
        if not _ordinary_a_share(ts_code) or not trade_date:
            continue
        deduped[(ts_code, trade_date)] = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "name": str(row.get("name") or ""),
            "type": str(row.get("type") or "") or None,
            "type_name": str(row.get("type_name") or "") or None,
            "fetched_at": fetched_at,
        }
    return [deduped[key] for key in sorted(deduped)]


def _source_quality(
    master: list[dict[str, Any]],
    open_dates: list[str],
    names: list[dict[str, Any]],
    st_rows: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    st_source_mode: str,
) -> dict[str, Any]:
    codes = {row["ts_code"] for row in master}
    open_set = set(open_dates)
    errors: list[dict[str, Any]] = []
    if not master:
        errors.append({"code": "stock_basic_empty"})
    if not open_dates:
        errors.append({"code": "trade_calendar_empty"})
    requires_name_intervals = (
        start < DIRECT_ST_COVERAGE_START
        or st_source_mode == "namechange_effective_interval_fallback"
    )
    if requires_name_intervals and not names:
        errors.append({"code": "namechange_empty_for_requested_range"})
    if (
        max(start, DIRECT_ST_COVERAGE_START) <= end
        and st_source_mode == "stock_st_daily"
        and not st_rows
    ):
        errors.append({"code": "stock_st_empty_for_direct_range"})
    unknown_st = [row for row in st_rows if row["ts_code"] not in codes]
    closed_st = [row for row in st_rows if row["trade_date"] not in open_set]
    if unknown_st:
        errors.append({"code": "stock_st_unknown_symbol", "count": len(unknown_st)})
    if closed_st:
        errors.append({"code": "stock_st_non_trading_date", "count": len(closed_st)})
    return {
        "status": "PASS" if not errors else "BLOCKED",
        "errors": errors,
        "policy": {
            "pre_2017": "complete_namechange_effective_interval",
            "from_2017": (
                "stock_st_daily_membership"
                if st_source_mode == "stock_st_daily"
                else "complete_namechange_event_snapshot_fallback"
            ),
            "st_source_mode": st_source_mode,
            "fallback_non_st_rule": (
                "no_effective_risk_name_event"
                if st_source_mode == "namechange_effective_interval_fallback"
                else None
            ),
            "current_name_backfill": False,
            "delisted_effective": "trade_date_after_delist_date",
        },
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
    }


def _write_daily_rows(
    conn: sqlite3.Connection,
    master: list[dict[str, Any]],
    open_dates: list[str],
    names: list[dict[str, Any]],
    st_rows: list[dict[str, Any]],
    end: date,
    *,
    st_source_mode: str,
    progress: Progress,
) -> tuple[int, list[dict[str, str]]]:
    intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in names:
        intervals[row["ts_code"]].append(row)
    for values in intervals.values():
        values.sort(key=lambda item: (item["start_date"], item["end_date"] or "9999-12-31"))
    direct_st = {(row["ts_code"], row["trade_date"]) for row in st_rows}
    direct_boundary = DIRECT_ST_COVERAGE_START.isoformat()
    batch: list[tuple[Any, ...]] = []
    written = 0
    unresolved: list[dict[str, str]] = []
    total = max(1, len(master))
    for index, item in enumerate(master):
        listed = item["list_date"]
        delisted = item["delist_date"]
        for trade_date in open_dates:
            if trade_date < listed:
                continue
            is_delisted = bool(delisted and trade_date > delisted)
            if trade_date >= direct_boundary and st_source_mode == "stock_st_daily":
                is_st = (item["ts_code"], trade_date) in direct_st
                source = "tushare_stock_st_v1"
            else:
                matched = [
                    row
                    for row in intervals.get(item["ts_code"], [])
                    if row["start_date"] <= trade_date
                    and (not row["end_date"] or trade_date <= row["end_date"])
                ]
                if len(matched) > 1:
                    risk_values = {
                        bool(_RISK_NAME.search(str(value["name"])))
                        for value in matched
                    }
                    if len(risk_values) > 1:
                        unresolved.append(
                            {
                                "ts_code": item["ts_code"],
                                "trade_date": trade_date,
                                "reason": "conflicting_risk_intervals",
                            }
                        )
                        if len(unresolved) >= 10_000:
                            return written, unresolved
                        continue
                    is_st = risk_values.pop()
                    source = "tushare_namechange_overlap_consensus_v1"
                elif matched:
                    is_st = bool(_RISK_NAME.search(str(matched[0]["name"])))
                    source = "tushare_namechange_v1"
                elif st_source_mode == "namechange_effective_interval_fallback":
                    # The globally paginated namechange snapshot is an event
                    # history, not a daily name dimension. Absence from every
                    # effective risk-name interval means no historical ST/PT
                    # event on that date; it does not backfill a current name.
                    is_st = False
                    source = "tushare_namechange_event_absence_v1"
                else:
                    unresolved.append(
                        {
                            "ts_code": item["ts_code"],
                            "trade_date": trade_date,
                            "reason": "missing_interval",
                        }
                    )
                    if len(unresolved) >= 10_000:
                        return written, unresolved
                    continue
            batch.append(
                (
                    item["exchange"],
                    item["stock_code"],
                    trade_date,
                    int(is_st),
                    int(is_delisted),
                    source,
                    end.isoformat(),
                )
            )
            if len(batch) >= 50_000:
                conn.executemany(
                    """INSERT INTO equity_status_daily(
                           exchange,stock_code,trade_date,is_st,is_delisted,source,source_as_of
                       ) VALUES(?,?,?,?,?,?,?)""",
                    batch,
                )
                written += len(batch)
                batch.clear()
                conn.commit()
        if index % 100 == 0:
            progress("展开逐日点时状态", 0.48 + 0.42 * (index + 1) / total)
    if batch:
        conn.executemany(
            """INSERT INTO equity_status_daily(
                   exchange,stock_code,trade_date,is_st,is_delisted,source,source_as_of
               ) VALUES(?,?,?,?,?,?,?)""",
            batch,
        )
        written += len(batch)
        conn.commit()
    return written, unresolved


def _reconcile_market_bar_coverage(
    conn: sqlite3.Connection,
    *,
    raw_equity_db: Path,
    start_date: str,
    end_date: str,
    master: list[dict[str, Any]],
    names: list[dict[str, Any]],
    st_rows: list[dict[str, Any]],
    st_source_mode: str,
    source_as_of: str,
) -> tuple[int, list[dict[str, str]]]:
    """Fill status keys present in formal bars but absent from ``stock_basic`` expansion.

    Tushare's current ``stock_basic`` snapshot occasionally omits an old security
    or reports a listing boundary a few sessions later than the formal bar store.
    The bar key is used only to define the historical universe. ST classification
    still comes exclusively from the frozen daily ST source or the complete
    historical ``namechange`` event snapshot; current security names are never
    consulted.
    """

    raw_path = raw_equity_db.resolve(strict=False)
    if not raw_path.is_file():
        raise FileNotFoundError(f"raw equity database not found: {raw_path}")
    before = raw_path.stat()
    uri = raw_path.as_uri() + "?mode=ro&immutable=1"
    conn.execute("ATTACH DATABASE ? AS raw_market", (uri,))
    try:
        missing = conn.execute(
            """
            SELECT r.ts_code, substr(r.ts_code, 1, 6), substr(r.ts_code, 8, 2), r.trade_date
              FROM raw_market.equity_daily_raw AS r
             WHERE r.trade_date BETWEEN ? AND ?
               AND (
                    (substr(r.ts_code, 8, 2)='SH' AND (
                           substr(r.ts_code, 1, 6) GLOB '600*'
                        OR substr(r.ts_code, 1, 6) GLOB '601*'
                        OR substr(r.ts_code, 1, 6) GLOB '603*'
                        OR substr(r.ts_code, 1, 6) GLOB '605*'
                        OR substr(r.ts_code, 1, 6) GLOB '688*'
                        OR substr(r.ts_code, 1, 6) GLOB '689*'
                    ))
                 OR (substr(r.ts_code, 8, 2)='SZ' AND (
                           substr(r.ts_code, 1, 6) GLOB '000*'
                        OR substr(r.ts_code, 1, 6) GLOB '001*'
                        OR substr(r.ts_code, 1, 6) GLOB '002*'
                        OR substr(r.ts_code, 1, 6) GLOB '003*'
                        OR substr(r.ts_code, 1, 6) GLOB '300*'
                        OR substr(r.ts_code, 1, 6) GLOB '301*'
                    ))
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM main.equity_status_daily AS s
                    WHERE s.exchange=substr(r.ts_code, 8, 2)
                      AND s.stock_code=substr(r.ts_code, 1, 6)
                      AND s.trade_date=r.trade_date
               )
             ORDER BY r.ts_code, r.trade_date
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        conn.execute("DETACH DATABASE raw_market")
    after = raw_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("formal raw equity database changed during read-only status reconciliation")

    intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in names:
        intervals[row["ts_code"]].append(row)
    direct_st = {(row["ts_code"], row["trade_date"]) for row in st_rows}
    master_by_code = {row["ts_code"]: row for row in master}
    direct_boundary = DIRECT_ST_COVERAGE_START.isoformat()
    payload: list[tuple[Any, ...]] = []
    unresolved: list[dict[str, str]] = []
    for ts_code, stock_code, exchange, trade_date in missing:
        if str(trade_date) >= direct_boundary and st_source_mode == "stock_st_daily":
            is_st = (str(ts_code), str(trade_date)) in direct_st
            source = "tushare_stock_st_bar_reconcile_v1"
        else:
            matched = [
                row
                for row in intervals.get(str(ts_code), [])
                if row["start_date"] <= str(trade_date)
                and (not row["end_date"] or str(trade_date) <= row["end_date"])
            ]
            risk_values = {bool(_RISK_NAME.search(str(row["name"]))) for row in matched}
            if len(risk_values) > 1:
                unresolved.append(
                    {
                        "ts_code": str(ts_code),
                        "trade_date": str(trade_date),
                        "reason": "conflicting_risk_intervals_during_bar_reconcile",
                    }
                )
                continue
            is_st = risk_values.pop() if risk_values else False
            source = (
                "tushare_namechange_bar_reconcile_v1"
                if matched
                else "tushare_namechange_event_absence_bar_reconcile_v1"
            )
        master_row = master_by_code.get(str(ts_code), {})
        delist_date = str(master_row.get("delist_date") or "")
        is_delisted = bool(delist_date and str(trade_date) > delist_date)
        payload.append(
            (
                str(exchange),
                str(stock_code),
                str(trade_date),
                int(is_st),
                int(is_delisted),
                source,
                source_as_of,
            )
        )
    if payload:
        conn.executemany(
            """INSERT INTO equity_status_daily(
                   exchange,stock_code,trade_date,is_st,is_delisted,source,source_as_of
               ) VALUES(?,?,?,?,?,?,?)""",
            payload,
        )
        conn.commit()
    return len(payload), unresolved


def _counts(
    master: list[dict[str, Any]],
    open_dates: list[str],
    names: list[dict[str, Any]],
    st_rows: list[dict[str, Any]],
    daily: int,
) -> dict[str, int]:
    return {
        "symbols": len(master),
        "open_dates": len(open_dates),
        "namechange_rows": len(names),
        "stock_st_rows": len(st_rows),
        "daily_status_rows": daily,
    }


def _ordinary_a_share(ts_code: str) -> bool:
    if len(ts_code) != 9 or ts_code[6] != ".":
        return False
    code, exchange = ts_code.split(".", 1)
    return (exchange == "SH" and code.startswith(_SH_PREFIXES)) or (
        exchange == "SZ" and code.startswith(_SZ_PREFIXES)
    )


def _month_windows(start: date, end: date) -> Iterable[tuple[date, date]]:
    current = start.replace(day=1)
    while current <= end:
        following = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(start, current), min(end, following - timedelta(days=1))
        current = following


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip().replace("-", "")
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _compact(value: date) -> str:
    return value.strftime("%Y%m%d")


def _clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if value is None or (isinstance(value, float) and pd.isna(value)):
            result[str(key)] = None
        elif hasattr(value, "item"):
            result[str(key)] = value.item()
        else:
            result[str(key)] = value
    return result


def _rows_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        list(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wait(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _remove_staging_database(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()


__all__ = [
    "DIRECT_ST_COVERAGE_START",
    "SCHEMA_VERSION",
    "equity_status_status",
    "import_equity_status_rows",
    "initialize_equity_status_database",
    "load_equity_status_readonly",
    "refresh_equity_status_daily",
]
