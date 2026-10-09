"""Linear, bounded hover metrics, computed once for the requested chart.

Changes are fractional returns. Volume ratios use five preceding open sessions
or trading weeks; unknown gaps never silently become zero-volume suspensions.
"""
from guanlan_domain.observer_math.indicators import _finite; from guanlan_domain.observer_math.indicators import week_key


def enrich(rows, period='daily', calendar=None, suspensions=None, *, volume_field='volume'):
    rows = [dict(r) for r in rows]
    if not rows:
        return rows
    if period not in ('daily','weekly'):
        raise ValueError('无效图表周期')
    by_day = {r['trade_date']:r for r in rows}
    if calendar:
        if period == 'daily':
            slots = list(calendar.sessions(rows[0]['trade_date'], rows[-1]['trade_date']))
            by_slot = by_day
        else:
            slots = [s['week_start'] for s in calendar.weeks(rows[0].get('period_start') or rows[0]['trade_date'],rows[-1]['trade_date'])]
            by_slot = {week_key(r['trade_date']):r for r in rows}
    else:
        slots = [r['trade_date'] if period == 'daily' else week_key(r['trade_date']) for r in rows]
        by_slot = dict(zip(slots,rows))
    positions = {day:i for i,day in enumerate(slots)}
    for row in rows:
        key = row['trade_date'] if period == 'daily' else week_key(row['trade_date'])
        index = positions.get(key, -1)
        previous = by_slot.get(slots[index-1],{}) if index > 0 else {}
        price, last = row.get('close'), previous.get('close')
        change = row.get('change_pct') if period == 'daily' else None
        if not _finite(change) and period == 'daily':
            change = row.get('daily_return')
        if not _finite(change):
            change = price / last - 1 if _finite(price) and _finite(last) and last > 0 else None
        row['change_pct'] = change
        row['change_reason'] = None if _finite(change) else '缺少可比前收盘价'
        row['volume_ratio'] = None
        row['volume_ratio_reason'] = '前5个交易' + ('日' if period == 'daily' else '周') + '行情不足或缺失'
        volume = row.get(volume_field)
        if index >= 5 and _finite(volume) and volume >= 0 and row.get('status') != 'missing' and not row.get('calendar_gap'):
            prior = []
            for day in slots[index-5:index]:
                other = by_slot.get(day,{})
                value = other.get(volume_field, 0 if period == 'daily' and day in (suspensions or {}) else None)
                if not _finite(value) or value < 0 or other.get('status') == 'missing' or other.get('calendar_gap'):
                    break
                prior.append(value)
            if len(prior) == 5:
                average = sum(prior)/5
                row['volume_ratio_reason'] = '前5期平均成交量为零' if average <= 0 else None
                if average > 0:
                    row['volume_ratio'] = volume/average
    return rows
