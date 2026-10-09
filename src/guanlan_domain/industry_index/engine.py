"""Pure daily equal-weight observation math. No database, clock, GUI or network."""
import math

METHOD = 'industry30_current_daily_equal_envelope_v2_confirmed_halts'
BASE = 1000.0


def number(value, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def price_reason(current, previous):
    if current is None: return 'MISSING_CURRENT_SESSION'
    if previous is None: return 'MISSING_PREVIOUS_SESSION'
    if not number(current.get('adj_factor'), True) or not number(previous.get('adj_factor'), True):
        return 'MISSING_OR_INVALID_FACTOR'
    if not number(previous.get('close'), True): return 'INVALID_PREVIOUS_CLOSE'
    if any(not number(current.get(k), True) for k in ('open', 'high', 'low', 'close')):
        return 'INVALID_OHLC'
    if current['high'] < max(current['open'], current['close'], current['low']) or current['low'] > min(current['open'], current['close']):
        return 'INVALID_OHLC_ORDER'
    return None


def _date(text):
    if not text: return None
    return f'{text[:4]}-{text[4:6]}-{text[6:]}' if len(text) == 8 else text


def calculate_group(group, market):
    dates, quotes = market['dates'], market['quotes']
    if dates != sorted(set(dates)) or not set(market['display_dates']) <= set(dates):
        raise ValueError('INVALID_CALENDAR')
    members = {m['code']: m for m in group['members']}
    excluded, eligible, unknown = [], {}, set()
    for code in sorted(members):
        meta = market['metadata'].get(code)
        if meta is None or not _date(meta.get('list_date')):
            unknown.add(code)
            excluded.append({'code': code, 'reason': 'SECURITY_IDENTITY_UNKNOWN'})
            continue
        if meta['list_status'] != 'L':
            excluded.append({'code': code, 'reason': 'NOT_CURRENTLY_LISTED'}); continue
        eligible[code] = _date(meta['list_date'])
    points = []
    level, segment, last_valid, broken = None, 0, False, False
    positions = {day: i for i, day in enumerate(dates)}
    for day in market.get('calculation_dates', market['display_dates']):
        position = positions[day]
        if position == 0: raise ValueError('PREVIOUS_SESSION_REQUIRED')
        previous_day = dates[position - 1]
        codes = sorted(code for code, listing in eligible.items() if listing < day)
        n = len(codes)
        point = dict(trade_date=day, status='empty' if not n else 'gap', segment_id=None,
                     open=None, high=None, low=None, close=None, daily_return=None,
                     expected_members=n, observed_members=0, coverage=None,
                     volume=None, amount=None, mean_volume=None, relative_volume_20=None,
                     volume_coverage=None, relative_volume_coverage=None,
                     missing=[], volume_missing=[], relative_volume_missing=[], suspended_members=0,
                     pre_listing_members=sum(listing >= day for listing in eligible.values()))
        ratios = {k: [] for k in ('open', 'high', 'low', 'close')}
        volumes, amounts, relative = [], [], []
        for code in codes:
            history = quotes.get(code, {})
            current, previous = history.get(day), history.get(previous_day)
            if current and current.get('valuation_kind') == 'confirmed_suspension': point['suspended_members'] += 1
            reason = price_reason(current, previous)
            if reason:
                point['missing'].append({'code': code, 'reason': reason})
            else:
                denominator = previous['close'] * previous['adj_factor']
                values = ({k: current[k] * current['adj_factor'] / denominator for k in ratios}
                          if number(denominator, True) else {})
                if values and all(number(v, True) for v in values.values()):
                    for k, v in values.items(): ratios[k].append(v)
                else: point['missing'].append({'code': code, 'reason': 'INVALID_ADJUSTED_RATIO'})
            if current and number(current.get('vol_lot')) and number(current.get('amount_thousand_cny')):
                volumes.append(current['vol_lot'] * 100)
                amounts.append(current['amount_thousand_cny'] * 1000)
            else:
                point['volume_missing'].append({'code': code, 'reason': 'MISSING_OR_INVALID_VOLUME_AMOUNT'})
            preceding = [history.get(d) for d in dates[max(0, position - 20):position]]
            if (current and number(current.get('vol_lot')) and len(preceding) == 20
                    and all(r and number(r.get('vol_lot')) for r in preceding)):
                avg = math.fsum(r['vol_lot'] for r in preceding) / 20
                if avg > 0:
                    relative.append(current['vol_lot'] / avg)
                else: point['relative_volume_missing'].append({'code': code, 'reason': 'ZERO_BASE_VOLUME'})
            else: point['relative_volume_missing'].append({'code': code, 'reason': 'INCOMPLETE_20_SESSION_VOLUME'})
        observed = len(ratios['close'])
        point.update(observed_members=observed, coverage=observed / n if n else None,
                     volume_coverage=len(volumes) / n if n else None,
                     relative_volume_coverage=len(relative) / n if n else None)
        if n and len(volumes) == n:
            point.update(volume=math.fsum(volumes), amount=math.fsum(amounts),
                         mean_volume=math.fsum(volumes) / n)
        if n and len(relative) == n: point['relative_volume_20'] = math.fsum(relative) / n
        if n and observed == n and not broken:
            if level is None: level, segment = BASE, 1
            values = {k: level * math.fsum(v) / n for k, v in ratios.items()}
            if not all(number(v, True) for v in values.values()):
                raise ValueError('NONFINITE_INDEX_LEVEL')
            point.update(**values, daily_return=math.fsum(ratios['close']) / n - 1,
                         segment_id=f'{group["id"]}:{segment}', status='ok',
                         segment_start=not last_valid, base_date=previous_day if not last_valid else None,
                         previous_close=level)
            level, last_valid = values['close'], True
        else:
            last_valid = False
            if n:
                if broken and observed == n:
                    point['missing'].append({'code': '', 'reason': 'UNRESOLVED_EARLIER_SESSION'})
                broken = True
        points.append(point)
    calculation_points = points
    display = set(market['display_dates'])
    points = [p for p in points if p['trade_date'] in display]
    uninterrupted = bool(points) and all(p['status'] == 'ok' for p in points) and segment == 1
    stats = dict(classified_members=len(members), current_listed_members=len(eligible),
                 unknown_identity_members=len(unknown), excluded_members=len(excluded),
                 verified_members=sum(m.get('status') == 'verified' for m in members.values()),
                 source_supported_members=sum(m.get('status') == 'source_supported' for m in members.values()),
                 unverified_members=sum(m.get('status') == 'unverified' for m in members.values()),
                 sessions=len(points), valid_sessions=sum(p['status'] == 'ok' for p in points),
                 gap_sessions=sum(p['status'] == 'gap' for p in points), segments=segment,
                 year_return=points[-1]['close'] / points[0]['previous_close'] - 1 if uninterrupted and not unknown else None,
                 suspended_member_sessions=sum(p['suspended_members'] for p in points),
                 minimum_coverage=min((p['coverage'] for p in points if p['coverage'] is not None), default=None))
    return dict(id=group['id'], name=group['name'], members=list(members.values()),
                excluded=excluded, points=points, stats=stats,
                high_low_kind='synthetic_envelope_not_intraday_extrema',
                membership_mode='current_members_historical_backcast', method_version=METHOD,
                calculation_points=calculation_points)


def calculate(snapshot, market):
    return [calculate_group(group, market) for group in snapshot['groups']]
