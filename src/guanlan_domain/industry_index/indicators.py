"""Shared versioned pure formulas; no runtime imports from upstream source files."""
import hashlib
from pathlib import Path
import guanlan_domain.observer_math as observer_math


def calculator():
    return observer_math


def provenance():
    root=Path(observer_math.__file__).parent
    return {'package_version':observer_math.VERSION,
            **{name:hashlib.sha256((root/name).read_bytes()).hexdigest()
               for name in ('indicators.py','prices.py','provenance.json')}}


def enrich(group,calendar=None):
    history = group.pop('calculation_points')
    if any(p['status'] == 'gap' for p in history):
        raise ValueError('UNRESOLVED_MARKET_GAP:' + group['id'])
    valid = [p for p in history if p['status'] == 'ok']
    start = group['points'][0]['trade_date'] if group['points'] else '9999-12-31'
    calc = calculator()
    daily, weekly = calc.chart_rows(valid, 'daily',calendar=calendar), calc.chart_rows(valid, 'weekly',calendar=calendar)
    # ETF already defines weekly OHLC and total volume/amount. Aggregate our extra measures explicitly.
    weeks = {}
    for point in valid: weeks.setdefault(calc.week_key(point['trade_date']), []).append(point)
    for row in weekly:
        days = weeks.get(row['week_start'],[])
        if row.get('calendar_gap') or not days:
            row.update(mean_volume=None,relative_volume_20=None,daily_return=None,suspended_members=None)
            continue
        for field in ('mean_volume', 'relative_volume_20'):
            values = [p[field] for p in days]
            row[field] = (sum(values) / len(values) if field == 'relative_volume_20' else sum(values)) if all(v is not None for v in values) else None
        for field in ('volume', 'amount'):
            if any(p[field] is None for p in days): row[field] = None
        row['daily_return'] = row['close'] / days[0]['previous_close'] - 1
        row['suspended_members'] = sum(p['suspended_members'] for p in days)
    group['points'] = [row for row in daily if row['trade_date'] >= start]
    group['weekly_points'] = [row for row in weekly if row['trade_date'] >= start]
    group['calculation_start'] = valid[0]['trade_date'] if valid else None
    return group
