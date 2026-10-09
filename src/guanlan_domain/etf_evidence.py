"""Conservative, source-preserving ETF exposure calculations."""
from __future__ import annotations
import math
import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

def weight(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and 0 <= result <= 100 else None

def index_identity(profile: Mapping[str, Any]) -> str | None:
    raw = str(profile.get('tracking_index_code') or '').strip()
    provider = str(profile.get('index_provider') or '').strip()
    if '.' in raw:
        raw, suffix = raw.rsplit('.', 1)
        provider = provider or suffix
    for label, code in (('中证', 'CSI'), ('深圳证券信息', 'CNI'), ('国证', 'CNI'), ('上海证券交易所', 'SSE'), ('上交所', 'SSE'), ('深圳证券交易所', 'SZSE'), ('深交所', 'SZSE')):
        if label in provider:
            provider = code
            break
    if not provider:
        name = str(profile.get('tracking_index_name') or '')
        provider = 'CSI' if '中证' in name else 'CNI' if '国证' in name else 'SSE' if '上证' in name else 'SZSE' if '深证' in name else ''
    return f'{provider.upper()}:{raw}' if raw and provider else None

def resolve_index_profile(profile: dict, catalog: Sequence[Mapping[str, Any]]) -> bool:
    """Resolve a source-qualified code from the authoritative index catalog.

    A code suffix is a data-source identifier, not an inferred index provider.
    Ambiguous bare codes remain unresolved instead of selecting a market.
    """
    raw = str(profile.get('tracking_index_code') or '').strip().upper()
    candidates = [r for r in catalog if str(r.get('ts_code') or '').split('.')[0].upper() == raw.split('.')[0]] if raw else []
    exact = [r for r in candidates if str(r.get('ts_code') or '').upper() == raw]
    named = [r for r in candidates if r.get('name') == profile.get('tracking_index_name')]
    candidates = list({str(r.get('ts_code')): r for r in exact or named or candidates}.values())
    if len(candidates) != 1:
        return False
    row = dict(candidates[0])
    profile['tracking_index_code'] = row['ts_code']
    profile['index_provider'] = row.get('publisher') or profile.get('index_provider')
    profile['index_metadata'] = {**row, **(profile.get('index_metadata') or {})}
    if not profile.get('tracking_index_name'):
        profile['tracking_index_name'] = row.get('name')
    return True

def exposure_summary(holdings: Sequence[Mapping[str, Any]], memberships: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Weights stay in portfolio percentage points; unknown weight is never filled.

    Memberships may overlap across themes. A stock contributes once per group,
    not once per provenance record. Group exposure is a lower bound, not purity.
    """
    by_code: dict[str, float] = {}
    missing = set()
    conflicts = set()
    for position, item in enumerate(holdings):
        raw = str(item.get('stock_code') or '').strip().upper()
        code, _, suffix = raw.partition('.')
        market = str(item.get('stock_exchange') or item.get('exchange') or item.get('market') or suffix).upper()
        mainland = bool(re.fullmatch('\\d{6}', code)) and market not in {'HK', 'HKG', 'SEHK', '港股', 'US', 'USA'} and (not suffix or suffix in {'SH', 'SZ', 'BJ'})
        if not mainland:
            code = f'unmatched:{market}:{raw or position}'
        value = weight(item.get('weight_pct'))
        if value is None:
            missing.add(code)
        elif code in by_code and by_code[code] != value:
            conflicts.add(code)
        else:
            by_code[code] = value
    for code in conflicts:
        by_code.pop(code, None)
    scores: dict[str, float] = defaultdict(float)
    matched = 0.0
    for code, value in by_code.items():
        groups = set(memberships.get(code, []))
        if groups:
            matched += value
        for group in groups:
            scores[group] += value
    disclosed = sum(by_code.values())
    valid = disclosed <= 100.01 and (not conflicts)
    return {'weight_valid': valid, 'disclosed_weight_pct': round(disclosed, 6), 'matched_weight_pct': round(matched, 6), 'unobserved_weight_pct': round(max(0.0, 100 - disclosed), 6), 'missing_weight_count': len(missing), 'conflicting_weight_count': len(conflicts), 'holding_count': len(by_code), 'exposures': [{'group_id': group, 'exposure_lower_bound_pct': round(value, 6)} for group, value in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))] if valid else [], 'interpretation': 'observed_portfolio_weight_lower_bound; overlapping themes are not additive'}
