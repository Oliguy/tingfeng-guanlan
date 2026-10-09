"""Read-only weekly signal extensions, independent of collection or publication."""
from datetime import date,timedelta
import math

def consecutive_above(weeks,calendar=None):
    """Latest strict MA30 run; an unobserved preceding week yields a lower bound."""
    count=0;newer_start=None
    for row in reversed(weeks):
        day=date.fromisoformat(row['trade_date']);start=day-timedelta(days=day.weekday())
        if newer_start is not None and not (calendar.adjacent_weeks(start.isoformat(),newer_start.isoformat()) if calendar else (newer_start-start).days==7):
            break
        close,ma=row.get('close'),row.get('thirty_week_ma')
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (close,ma)):
            break
        if close<=ma:
            return {'consecutive_above_weeks':count,'streak_is_lower_bound':False}
        count+=1;newer_start=start
    return {'consecutive_above_weeks':count if count else None,'streak_is_lower_bound':count>0}

def recent_pair(weeks,calendar=None):
    pair=[]
    for row in weeks[-2:]:
        close,ma=row.get('close'),row.get('thirty_week_ma')
        valid=all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (close,ma))
        day=date.fromisoformat(row['trade_date']);start=day-timedelta(days=day.weekday())
        pair.append({'trade_date':row['trade_date'],'week_start':start.isoformat(),'above':close>ma if valid else None})
    consecutive=len(pair)==2 and (calendar.adjacent_weeks(pair[0]['week_start'],pair[1]['week_start']) if calendar else (date.fromisoformat(pair[1]['week_start'])-date.fromisoformat(pair[0]['week_start'])).days==7)
    return {'recent_weeks':pair,'two_weeks_above':consecutive and all(r['above'] is True for r in pair)}

def etf_pair(rows,calendar=None):
    from guanlan_domain.observer_math import weekly_rows; from guanlan_domain.observer_math import continuous_adjust_bars
    from guanlan_domain.observer_math.indicators import aggregate_weekly; from guanlan_domain.observer_math.indicators import _finite
    adjusted,_=continuous_adjust_bars(rows);weeks=aggregate_weekly(adjusted,calendar)
    for i in range(len(weeks)):
        closes=[r.get('close') for r in weeks[max(0,i-29):i+1]]
        weeks[i]['thirty_week_ma']=sum(closes)/30 if len(closes)==30 and all(_finite(v) and v>0 for v in closes) else None
    return strength(weeks,calendar)

def strength(weeks,calendar=None):
    """Same ETF five-week signal, using already preheated ETF MA30 values."""
    from guanlan_domain.observer_math.indicators import align_weeks; from guanlan_domain.observer_math.indicators import _finite
    weeks=align_weeks(weeks,calendar)
    recent = [p for p in weeks[-5:] if _finite(p.get('close')) and _finite(p.get('thirty_week_ma')) and p['thirty_week_ma']>0]
    above = sum(p['close'] > p['thirty_week_ma'] for p in recent)
    total = len(recent)
    last = weeks[-1] if weeks else {}
    avg = last.get('thirty_week_ma')
    valid=_finite(last.get('close')) and _finite(avg) and avg>0
    above_now = last['close'] > avg if valid else None
    return {**recent_pair(weeks,calendar), **consecutive_above(weeks,calendar), 'above': above, 'total': total, 'available': len(weeks),
            'strength_score': above if total == 5 else None, 'above_now': above_now,
            'distance_30w': last['close'] / avg - 1 if valid else None,
            'weekly_return': last.get('daily_return'), 'weekly_volume': last.get('mean_volume'),
            'shade': min(.3, .07 + (above if above_now else total - above) * .046) if total == 5 else 0,
            'as_of_date': last.get('trade_date'), 'method_version': 'ma30w_trading_week_v2' if calendar else 'ma30w_asof_day_v1'}


def price_strength(daily,calendar=None):
    """MA30 weekly projection identical to chart_rows, without unused overlays.

    Inputs are already adjusted, validated OHLC rows. Use the full preheat
    history; the current unfinished week contributes its latest stored close.
    """
    from guanlan_domain.observer_math.indicators import aggregate_weekly; from guanlan_domain.observer_math.indicators import _finite
    weeks = aggregate_weekly(daily,calendar)
    closes = [row.get('close') for row in weeks]
    invalid = [0]
    for close in closes:
        invalid.append(invalid[-1] + (not _finite(close)))
    for i, row in enumerate(weeks):
        start = max(0, i - 29)
        window = closes[start:i + 1]
        row['thirty_week_ma'] = ((sum(window[:-1]) + window[-1]) / 30
                                 if len(window) == 30 and invalid[i + 1] == invalid[start] else None)
    return strength(weeks,calendar)
