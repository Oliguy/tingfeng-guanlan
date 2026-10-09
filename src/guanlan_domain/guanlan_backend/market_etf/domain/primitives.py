"""Pure rules; caller owns all input acquisition and persistence."""
from __future__ import annotations
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Mapping, Sequence
RULE_VERSION = 'observer_record_effective_v3'
DISPLAY_GAP_THRESHOLD = 0.2
LEADER_RULE_VERSION = 'monthly_complete_calendar_min_60_weeks_v3'
LEADER_MIN_HISTORY_WEEKS = 60

def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def normalize_memberships(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Restore recorded boundaries; do not turn retrospective extensions into facts."""
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    archived, conflicts = ([], [])
    for source in rows:
        row = dict(source)
        evidence = json.loads(row.get('evidence_json') or '{}')
        reconstruction = evidence.get('historical_reconstruction')
        if reconstruction:
            original_start = reconstruction.get('previous_effective_from')
            if not original_start:
                conflicts.append({'etf_code': row['etf_code'], 'reason': 'missing_original_effective_from', 'record': row})
                continue
            date.fromisoformat(original_start)
            if original_start < row['effective_from']:
                conflicts.append({'etf_code': row['etf_code'], 'reason': 'invalid_reconstruction_boundary', 'record': row})
                continue
            archived.append({'record': dict(source), 'reason': 'retrospective_extension_removed'})
            row['effective_from'] = original_start
        if row.get('effective_to') is not None and row['effective_to'] < row['effective_from']:
            archived.append({'record': dict(source), 'reason': 'empty_interval'})
            continue
        by_code[row['etf_code']].append(row)
    normalized = []
    for code, members in sorted(by_code.items()):
        boundaries = {row['effective_from'] for row in members}
        for row in members:
            if row.get('effective_to') and row['effective_to'] != '9999-12-31':
                boundaries.add((date.fromisoformat(row['effective_to']) + timedelta(days=1)).isoformat())
        dates = sorted(boundaries)
        for index, day in enumerate(dates):
            active = [row for row in members if row['effective_from'] <= day <= (row.get('effective_to') or '9999-12-31')]
            if not active:
                continue
            groups = {row['group_id'] for row in active}
            if len(groups) != 1:
                conflicts.append({'etf_code': code, 'from_date': day, 'group_ids': sorted(groups), 'reason': 'conflicting_recorded_primary_decisions'})
                continue
            finish = (date.fromisoformat(dates[index + 1]) - timedelta(days=1)).isoformat() if index + 1 < len(dates) else None
            if finish is None and all((row.get('effective_to') for row in active)):
                finish = max((row['effective_to'] for row in active))
            representative = min(active, key=lambda row: (row['effective_from'], row.get('created_at', '')))
            evidence = json.loads(representative.get('evidence_json') or '{}')
            evidence.pop('historical_reconstruction', None)
            lineage = sorted({item for row in active for item in json.loads(row.get('evidence_json') or '{}').get('normalization', {}).get('lineage', [digest(row)])})
            evidence['normalization'] = {'basis': 'record_effective', 'known_at': None, 'lineage': lineage}
            normalized.append({**representative, 'effective_from': day, 'effective_to': finish, 'status': 'active' if finish is None else 'superseded', 'method_version': RULE_VERSION, 'evidence_json': canonical(evidence)})
    return {'rows': normalized, 'archive': archived, 'conflicts': conflicts}

def daily_flow(current: Mapping[str, Any] | None, previous: Mapping[str, Any] | None) -> tuple[float | None, str]:
    if not current or not previous:
        return (None, 'missing_adjacent_share_observation')
    if current.get('conflict') or previous.get('conflict'):
        return (None, 'conflicting_share_observations')
    if any((item.get('observation_kind') != 'daily' or not item.get('source_url') or str(item.get('source_hash', '')).startswith('unverified:') for item in (current, previous))):
        return (None, 'source_not_verified_daily')
    if current.get('nav_date') != current.get('as_of_date') or current.get('nav') is None:
        return (None, 'nav_date_mismatch')
    if current.get('split_status') not in {'verified_none', 'verified_adjusted'}:
        return (None, 'split_not_verified')
    factor = current.get('split_factor') if current.get('split_status') == 'verified_adjusted' else 1.0
    if not factor or float(factor) <= 0 or any((item.get('shares_outstanding') is None for item in (current, previous))):
        return (None, 'invalid_shares_or_factor')
    return ((float(current['shares_outstanding']) - float(previous['shares_outstanding']) * float(factor)) * float(current['nav']), 'PASS')

def normalize_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip().replace('/', '-')
    if len(text) == 8 and text.isdigit():
        return f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return date.fromisoformat(text).isoformat()

def continuous_adjust_bars(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Back-adjust OHLC for display while retaining raw exchange values separately."""
    bars = [dict(row) for row in rows]
    if not bars:
        return ([], [])
    factors = [1.0] * len(bars)
    events: list[dict[str, Any]] = []
    running_factor = 1.0
    for index in range(len(bars) - 1, 0, -1):
        factors[index] = running_factor
        current_open = bars[index].get('open')
        previous_close = bars[index - 1].get('close')
        if current_open and previous_close:
            boundary_factor = float(current_open) / float(previous_close)
            if abs(boundary_factor - 1.0) > DISPLAY_GAP_THRESHOLD:
                events.append({'trade_date': bars[index]['trade_date'], 'factor': boundary_factor, 'previous_close': previous_close, 'current_open': current_open, 'reason': 'unit_price_discontinuity'})
                running_factor *= boundary_factor
    factors[0] = running_factor
    for bar, factor in zip(bars, factors):
        for field in ('open', 'high', 'low', 'close', 'prev_close'):
            if bar.get(field) is not None:
                bar[field] = float(bar[field]) * factor
        bar['display_adjustment_factor'] = factor
        bar['is_adjustment_boundary'] = any((event['trade_date'] == bar['trade_date'] for event in events))
    events.sort(key=lambda event: event['trade_date'])
    return (bars, events)
