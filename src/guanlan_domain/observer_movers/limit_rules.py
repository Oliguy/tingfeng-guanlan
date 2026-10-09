"""Point-in-date, local close-at-limit evidence. No database or network access."""
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from guanlan_domain.observer_movers.limits import finite; from guanlan_domain.observer_movers.limits import same_price; from guanlan_domain.observer_movers.limits import valid_limit

RULE_VERSION = 'guanlan_local_limit_v1_2'
TICK = Decimal('0.01')


def valid_bar(row):
    values = [row.get(k) for k in ('open', 'high', 'low', 'close')]
    if not all(finite(v) and v > 0 for v in values):
        return False
    o, h, l, c = values
    return (l <= min(o, c) <= max(o, c) <= h and finite(row.get('pre_close'))
            and row['pre_close'] > 0 and finite(row.get('vol_lot')) and row['vol_lot'] >= 0
            and finite(row.get('amount_thousand_cny')) and row['amount_thousand_cny'] >= 0)


def market_band(code, day, name=None, is_st=None):
    """Known ordinary listing bands. Earlier warning status needs dated evidence."""
    if day < '2026-07-06' and is_st is None:
        return None, 'historical_warning_status_unavailable'
    if not isinstance(code, str) or '.' not in code:
        return None, 'market_unknown'
    number, market = code.split('.', 1)
    if market == 'BJ':
        if day < '2021-11-15':return None, 'exchange_historical_rule_unverified'
        return Decimal('0.30'), None
    if market == 'SZ' and number[:3] in ('300', '301', '302'):
        return (Decimal('0.20') if day >= '2020-08-24' else Decimal('0.05') if is_st is True else Decimal('0.10')), None
    if market == 'SH' and number[:3] in ('688', '689'):
        return Decimal('0.20'), None
    if market in ('SZ', 'SH') and len(number) == 6 and number.isdigit():
        if name and name.upper().startswith('S') and not name.upper().startswith(('ST', 'S*ST')):
            return None, 'S_share_special_status_unverified'
        return Decimal('0.05') if is_st is True and day < '2026-07-06' else Decimal('0.10'), None
    return None, 'market_unknown'


def _limit(pre_close, band, market):
    result = Decimal(str(pre_close)) * (Decimal('1') + band)
    if market == 'BJ':
        return result.quantize(TICK, rounding=ROUND_FLOOR)
    return result.quantize(TICK, rounding=ROUND_HALF_UP)


def _verified_band(row, support):
    """Classify only a dated two-sided price pair, allowing one tick rounding."""
    if not support or support.get('status') != 'available' or not valid_limit(support.get('up_limit')) or not valid_limit(support.get('down_limit')):
        return None
    base = Decimal(str(row['pre_close']))
    market = row['ts_code'].rsplit('.', 1)[-1]
    for band in (Decimal('0.05'), Decimal('0.10'), Decimal('0.20'), Decimal('0.30')):
        up = _limit(base, band, market)
        down = base * (Decimal('1') - band)
        if abs(Decimal(str(support['up_limit'])) - up) <= TICK and abs(Decimal(str(support['down_limit'])) - down) <= TICK:
            return band
    return None


def decide(row, *, list_date=None, prior_quote_days=None, session_index=None,
           support=None, positive=False, name=None, is_st=None):
    """Return an evidenced daily state; absence of a KPH row is never negative."""
    day, code = row['trade_date'], row['ts_code']
    sources = []
    if support:
        sources.append({'kind': 'dated_limit_price', 'date': day,
                        'request_id': support.get('request_id')})
    if positive:
        sources.append({'kind': 'dated_positive_event', 'date': day})
    if not valid_bar(row):
        return {'status': 'unknown', 'up_limit': None, 'reason': 'invalid_ohlc_or_pre_close',
                'rule_version': RULE_VERSION, 'sources': sources}
    if row['vol_lot'] <= 0:
        return {'status': 'unknown', 'up_limit': None, 'reason': 'no_positive_trading_volume',
                'rule_version': RULE_VERSION, 'sources': sources}
    listed = str(list_date or '')
    listed = listed[:4] + '-' + listed[4:6] + '-' + listed[6:8] if len(listed) == 8 else listed
    if listed and listed > day:
        return {'status': 'unknown', 'up_limit': None, 'reason': 'before_known_listing_date',
                'rule_version': RULE_VERSION, 'sources': sources}
    early = False
    if listed and session_index and listed in session_index and day in session_index:
        early = session_index[day] - session_index[listed] < (1 if code.endswith('.BJ') else 5)
    elif (not listed or (session_index and listed > min(session_index))) and (prior_quote_days or 0) < 6:
        early = None
    band, reason = market_band(code, day, name, is_st)
    candidate = None
    if early is True:
        registration = code.endswith('.BJ') or code.startswith(('688','689')) or (code.startswith(('300','301','302')) and listed >= '2020-08-24') or listed >= '2023-04-10'
        if registration:candidate = {'status': 'unrestricted', 'up_limit': None, 'reason': 'initial_listing_window'}
        else:reason = 'legacy_initial_listing_rule_unverified'
    elif early is None:
        reason = 'listing_window_unverified'
    elif band is not None:
        calculated = _limit(row['pre_close'], band, code.rsplit('.', 1)[-1])
        if Decimal(str(row['close'])) > calculated + TICK:
            reason = 'close_above_ordinary_limit_special_day_unverified'
        elif Decimal(str(row['close'])) < Decimal(str(row['pre_close'])) * (Decimal('1') - band) - TICK:
            reason = 'close_below_ordinary_limit_special_day_unverified'
        else:
            candidate = {'status': 'confirmed_up' if same_price(row['close'], float(calculated)) else 'not_limit',
                         'up_limit': float(calculated), 'reason': None}
    verified_band = _verified_band(row, support)
    if verified_band is not None and band is not None and verified_band != band:
        candidate = None
    verified = None
    if support:
        state = support.get('status')
        if state == 'conflict':
            return {'status': 'conflict', 'up_limit': support.get('up_limit'), 'reason': 'dated_source_conflict',
                    'rule_version': RULE_VERSION, 'sources': sources}
        if state == 'unrestricted':
            verified = {'status': 'unrestricted', 'up_limit': None, 'reason': None}
        elif state == 'available' and valid_limit(support.get('up_limit')) and valid_limit(support.get('down_limit')):
            if (Decimal(str(row['high'])).quantize(TICK, rounding=ROUND_HALF_UP) > Decimal(str(support['up_limit']))
                    or Decimal(str(row['low'])).quantize(TICK, rounding=ROUND_HALF_UP) < Decimal(str(support['down_limit']))):
                return {'status': 'conflict', 'up_limit': support['up_limit'], 'reason': 'ohlc_outside_dated_limits',
                        'rule_version': RULE_VERSION, 'sources': sources}
            verified = {'status': 'confirmed_up' if same_price(row['close'], support['up_limit']) else 'not_limit',
                        'up_limit': support['up_limit'], 'reason': None}
    if verified and candidate and verified['status'] != candidate['status']:
        return {'status': 'conflict', 'up_limit': verified['up_limit'], 'reason': 'rule_and_source_disagree',
                'rule_version': RULE_VERSION, 'sources': sources}
    result = verified or candidate
    if positive and result and result['status'] in ('not_limit', 'unrestricted'):
        return {'status': 'conflict', 'up_limit': result['up_limit'], 'reason': 'positive_event_conflicts',
                'rule_version': RULE_VERSION, 'sources': sources}
    if not result and positive:
        result = {'status': 'confirmed_up', 'up_limit': None, 'reason': 'positive_event_only'}
    result = result or {'status': 'unknown', 'up_limit': None, 'reason': reason or 'dated_evidence_missing'}
    return {**result, 'band': str(verified_band or band) if (verified_band or (candidate and band)) else None,
            'rule_version': RULE_VERSION, 'sources': sources}


def daily_shape(row, fact):
    """Daily-only seal shape, with explicit uncertainty. No seal-time inference."""
    state = fact.get('status', 'unknown')
    if state in ('not_limit', 'unrestricted', 'suspended', 'not_listed'):
        return {'points': 0.0, 'min': 0.0, 'max': 0.0, 'kind': 'not_limit', 'unknown_reasons': []}
    unknown = {'points': None, 'min': 0.0, 'max': 20.0, 'kind': 'unknown',
               'unknown_reasons': [fact.get('reason') or 'closing_limit_unverified']}
    if state != 'confirmed_up':
        return unknown
    limit = fact.get('up_limit')
    if not row or not valid_bar(row) or row['vol_lot'] <= 0:
        return {**unknown, 'unknown_reasons': ['invalid_or_missing_daily_shape']}
    if not valid_limit(limit) or not same_price(row['close'], limit) or limit <= row['pre_close']:
        return {**unknown, 'unknown_reasons': ['effective_up_limit_unavailable']}
    if not same_price(row['high'], limit):
        return {**unknown, 'unknown_reasons': ['ohlc_and_up_limit_conflict']}
    one = all(same_price(row[k], limit) for k in ('open', 'high', 'low', 'close'))
    space = Decimal(str(limit)) - Decimal(str(row['pre_close']))
    body = abs(Decimal(str(row['close'])) - Decimal(str(row['open']))) / space
    shadow = (min(Decimal(str(row['open'])), Decimal(str(row['close']))) - Decimal(str(row['low']))) / space
    points = Decimal(20) if one else max(Decimal(0), Decimal(18)*(1-body)-Decimal(10)*shadow)
    return {'points': float(points), 'min': float(points), 'max': float(points),
            'kind': 'one_price' if one else 'other_limit', 'body_ratio': float(body),
            'lower_shadow_ratio': float(shadow), 'limit_space': float(space),
            'open': row['open'], 'high': row['high'], 'low': row['low'], 'close': row['close'],
            'pre_close': row['pre_close'], 'up_limit': limit,
            'formula': '20' if one else 'max(0,18*(1-body_ratio)-10*lower_shadow_ratio)',
            'unknown_reasons': [], 'sources': fact.get('sources', [])}
