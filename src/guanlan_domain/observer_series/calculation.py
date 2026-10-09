"""Pure weekly materialization; weekly OHLC is always unadjusted."""
from collections import deque
from datetime import date, timedelta
import math
from guanlan_domain.observer_math.indicators import week_key
from guanlan_domain.observer_series import METHOD


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def effective_factors(rows, kind, *, initial_factor=1.0, previous_close=None):
    if kind == 'stock':
        return {r['trade_date']: r.get('adj_factor') for r in rows}
    # Algebraically the existing ETF backward display rule, in a stable forward basis.
    factor, previous, result = initial_factor, ({'close':previous_close} if previous_close else None), {}
    for row in rows:
        if previous and row.get('open') and previous.get('close'):
            ratio = row['open'] / previous['close']
            if abs(ratio - 1.0) > .20:
                factor /= ratio
        result[row['trade_date']] = factor
        previous = row
    return result


def _state(close, prefix, before, day, week, *, valid=True):
    # prefix: [sum(previous29), valid_count, previous_streak, lower_bound,
    #          previous4 above/null states, prior_trading_week, available_before]
    total, count, streak, lower, recent, prior, available = prefix
    ma = (total + close) / 30 if valid and finite(close) and count == 29 else None
    above = close > ma if ma is not None and ma > 0 else None
    run = (streak + 1 if above else 0) if above is not None else None
    lower_now = bool(lower) if above else False
    flags = (recent + [above])[-5:]
    valid_flags = [v for v in flags if v is not None]
    n, wins = len(valid_flags), sum(v is True for v in valid_flags)
    pair = ([{'trade_date': before, 'week_start': prior, 'above': recent[-1]}]
            if recent and prior else []) + [{'trade_date': day, 'week_start': week, 'above': above}]
    return {'above_now': above, 'consecutive_above_weeks': run, 'streak_is_lower_bound': lower_now,
            'above': wins, 'total': n, 'strength_score': wins if n == 5 else None,
            'available': available + 1, 'two_weeks_above': len(pair) == 2 and all(p['above'] is True for p in pair),
            'recent_weeks': pair, 'distance_30w': close / ma - 1 if ma else None,
            'thirty_week_ma': ma, 'shade': min(.3, .07 + (wins if above else n-wins) * .046) if n == 5 else 0,
            'as_of_date': day, 'method_version': METHOD, 'weekly_return': None, 'weekly_volume': None}


def build_weeks(rows, calendar, *, kind='stock', suspensions=(), seed=None, range_start=None):
    """No adjusted OHLC is emitted or stored; canonical prices are temporary math."""
    if not rows:
        return []
    previous = seed[-1] if seed else None
    factors = effective_factors(rows, kind, initial_factor=previous['close_factor'] if previous else 1,
                                previous_close=previous['close'] if previous else None)
    observed = {r['trade_date']: r for r in rows}
    first, last = range_start or rows[0]['trade_date'], rows[-1]['trade_date']
    by_week = {}
    for row in rows:
        by_week.setdefault(week_key(row['trade_date']), []).append(row)
    rolling = {p: deque(maxlen=29) for p in ('raw', 'adjusted')}
    runs = {p: [0, True, []] for p in rolling}
    previous_day, previous_week, previous_price, previous_factor = None, None, None, None
    weeks = []
    available_before=0
    if seed:
        previous_day,previous_week=previous['trade_date'],previous['week_start']
        previous_price,previous_factor=previous['close'],previous['close_factor']
        available_before=previous['prefix']['raw'][6]+1
        for policy in rolling:
            for w in seed[-29:]:
                q=w['quality'];valid=not(q['missing'] or q['invalid'] or not q['known'] or (policy=='adjusted' and q['factor_missing']))
                value=w['close'] if policy=='raw' else w['close']*w['close_factor'] if finite(w['close']) and finite(w['close_factor']) else None
                rolling[policy].append(value if valid else None)
            sig=previous['signals'][policy]
            recent=(previous['prefix'][policy][4]+[sig['above_now']])[-4:]
            runs[policy]=[sig['consecutive_above_weeks'] or 0,sig['streak_is_lower_bound'] if sig['above_now'] is not None else True,recent]
    suspended = set(suspensions)
    for slot in calendar.weeks(first, last):
        key, sessions = slot['week_start'], slot['sessions']
        actual = by_week.get(key, [])
        missing, invalid, valuation = [], [], []
        for day in sessions:
            row = observed.get(day)
            if row is not None:
                valid = all(finite(row.get(k)) and row[k] > 0 for k in ('open', 'high', 'low', 'close'))
                if not valid:
                    invalid.append(day)
                valuation.append(row)
                previous_price, previous_factor = row.get('close'), factors.get(day)
            elif day in suspended and finite(previous_price):
                # Valuation evidence only. It never becomes a daily trade row.
                valuation.append({'trade_date': day, **{k: previous_price for k in ('open','high','low','close')},
                                  'volume': 0, 'amount': 0, 'valuation': True})
                factors[day] = previous_factor
            else:
                missing.append(day)
        end = valuation[-1]['trade_date'] if valuation else slot['last_session']
        vclose = valuation[-1].get('close') if valuation else None
        fac = factors.get(end)
        factor_missing = [r['trade_date'] for r in valuation if not finite(factors.get(r['trade_date'])) or factors[r['trade_date']] <= 0]
        end_week = (date.fromisoformat(key) + timedelta(days=6)).isoformat()
        full = calendar.weeks(key, end_week)
        planned = full[0]['last_session'] if full else None
        complete = bool(full and full[0]['known'] and planned <= last)
        gap = bool(missing or invalid or not slot['known'])
        points = actual or valuation
        usable = [r for r in points if all(finite(r.get(k)) for k in ('open','high','low','close'))]
        fvalues = {factors.get(r['trade_date']) for r in valuation}
        out = {'week_start': key, 'trade_date': end, 'period_start': points[0]['trade_date'] if points else key,
               'open': usable[0]['open'] if usable else None, 'high': max(r['high'] for r in usable) if usable else None,
               'low': min(r['low'] for r in usable) if usable else None, 'close': vclose,
               'volume': sum(r.get('volume') or 0 for r in actual), 'amount': sum(r.get('amount') or 0 for r in actual),
               'open_factor': factors.get(valuation[0]['trade_date']) if valuation else None,
               'close_factor': fac, 'mixed_factor': len(fvalues) != 1, 'planned_last_session': planned,
               'complete': complete, 'previous_day': previous_day, 'quality': {'missing': missing, 'invalid': invalid, 'factor_missing': factor_missing,
                   'suspended': [d for d in sessions if d in suspended and d not in observed], 'known': slot['known']},
               'prefix': {}, 'signals': {}}
        for policy, price in (('raw', vclose), ('adjusted', vclose*fac if finite(vclose) and finite(fac) and fac > 0 else None)):
            q = rolling[policy]
            valid_closes = [v for v in q if finite(v)]
            run, lower, recent = runs[policy]
            prefix = [sum(valid_closes), len(valid_closes) if len(q)==29 else len(valid_closes),
                      run, lower, list(recent[-4:]), previous_week, available_before+len(weeks)]
            out['prefix'][policy] = prefix
            valid = not gap and (policy == 'raw' or not factor_missing)
            signal = _state(price, prefix, previous_day, end, key, valid=valid)
            out['signals'][policy] = signal
            above = signal['above_now']
            runs[policy] = [signal['consecutive_above_weeks'] or 0,
                           signal['streak_is_lower_bound'] if above is not None else True,
                           (recent + [above])[-4:]]
            q.append(price if valid else None)
        weeks.append(out)
        previous_day, previous_week = end, key
    return weeks


def project(week, day, close, factor=1, *, policy='adjusted'):
    """An as-of day projection uses no later prices in its own week."""
    quality = week['quality']
    invalid = quality['missing'] + quality['invalid'] + (quality['factor_missing'] if policy=='adjusted' else [])
    valid = quality['known'] and not any(d <= day for d in invalid)
    price = close if policy=='raw' else close*factor if finite(close) and finite(factor) and factor>0 else None
    prefix = week['prefix'][policy]
    previous_day = week.get('previous_day')
    value = _state(price, prefix, previous_day, day, week['week_start'], valid=valid)
    value.update(quote_date=day, expected_date=day, provisional=bool(week['planned_last_session'] and day < week['planned_last_session']),
                 status='ready' if value['above_now'] is not None else 'insufficient' if prefix[6]<29 else 'unavailable')
    if not valid:
        value.update(status='unavailable', reason='交易周行情或复权因子有缺口')
    return value


def display_week(week, anchor, *, policy='adjusted', days=None):
    result = {k: week.get(k) for k in ('week_start','trade_date','period_start','open','high','low','close','volume','amount')}
    if not week['quality']['known'] or week['quality']['missing'] or week['quality']['invalid']:
        return {**result, **{k: None for k in ('open','high','low','close')}, 'status':'missing'}
    if policy == 'adjusted':
        if not finite(anchor) or anchor <= 0 or week['quality']['factor_missing']:
            return {**result, **{k: None for k in ('open','high','low','close')}, 'status':'missing'}
        if week['mixed_factor']:
            if days is None:
                raise ValueError('周内因子变化，必须提供该周原始日线')
            adjusted = [{**r, **{k:r[k]*r['effective_factor']/anchor for k in ('open','high','low','close')}} for r in days]
            if adjusted:
                result.update(open=adjusted[0]['open'], high=max(r['high'] for r in adjusted),
                              low=min(r['low'] for r in adjusted), close=adjusted[-1]['close'])
        else:
            for field in ('open','high','low','close'):
                result[field] = result[field]*week['close_factor']/anchor if result[field] is not None else None
    ma = week['signals'][policy]['thirty_week_ma']
    result['thirty_week_ma'] = ma/anchor if ma is not None and policy=='adjusted' else ma
    result['provisional'] = not week['complete']
    return result
