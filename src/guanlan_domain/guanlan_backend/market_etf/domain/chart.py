"""Pure display indicators, preserving the v16 browser chart calculation."""
from datetime import date, timedelta
import math

def _finite(value):
    return isinstance(value, (int, float)) and (not isinstance(value, bool)) and math.isfinite(value)

def moving_average(values, period):
    total, missing = (0.0, 0)
    output = []
    for index, value in enumerate(values):
        if _finite(value):
            total += value
        else:
            missing += 1
        if index >= period:
            old = values[index - period]
            if _finite(old):
                total -= old
            else:
                missing -= 1
        output.append(None if missing else total / min(period, index + 1))
    return output

def exponential_average(values, period):
    alpha = 2 / (period + 1)
    previous = None
    output = []
    for value in values:
        if not _finite(value):
            output.append(None)
            continue
        previous = value if previous is None else alpha * value + (1 - alpha) * previous
        output.append(previous)
    return output

def week_key(value):
    day = date.fromisoformat(value)
    return (day - timedelta(days=day.weekday())).isoformat()

def aggregate_weekly(rows):
    weeks = []
    for row in rows:
        key = week_key(row['trade_date'])
        if not weeks or weeks[-1]['week_start'] != key:
            weeks.append({**row, 'week_start': key, 'period_start': row['trade_date'], 'volume': row.get('volume') or 0, 'amount': row.get('amount') or 0})
        else:
            last = weeks[-1]
            last.update(trade_date=row['trade_date'], close=row.get('close'), high=max(last.get('high') or 0, row.get('high') or 0), low=min(last.get('low') or 0, row.get('low') or 0), volume=last['volume'] + (row.get('volume') or 0), amount=last['amount'] + (row.get('amount') or 0), is_adjustment_boundary=bool(last.get('is_adjustment_boundary') or row.get('is_adjustment_boundary')))
    return weeks

def chart_rows(daily, period):
    rows = aggregate_weekly(daily) if period == 'weekly' else [dict(row) for row in daily]
    closes = [row.get('close') for row in rows]
    averages = {n: moving_average(closes, n) for n in (3, 6, 12, 14, 24, 28, 30, 57, 114)}
    trend = exponential_average(exponential_average(closes, 10), 10)
    previous = []
    current_week = None
    last_close = None
    line = {}
    for row in daily:
        key = week_key(row['trade_date'])
        if current_week is not None and key != current_week:
            previous = (previous + [last_close])[-29:]
        current_week = key
        last_close = row.get('close')
        line[row['trade_date']] = (sum(previous) + last_close) / 30 if len(previous) == 29 and all((_finite(v) for v in previous)) and _finite(last_close) else None
    for index, row in enumerate(rows):

        def average_periods(periods):
            values = [averages[n][index] for n in periods]
            return sum(values) / len(values) if all((_finite(v) for v in values)) else None
        row.update(z_zhixing_short_trend=trend[index], z_zhixing_bull_bear=average_periods((14, 28, 57, 114)), ma30=averages[30][index], bbi=average_periods((3, 6, 12, 24)), thirty_week_ma=line.get(row['trade_date']))
    return rows
