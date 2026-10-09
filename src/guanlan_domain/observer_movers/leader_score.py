"""Deterministic, point-in-date Guanlan observation score. No I/O."""
from bisect import bisect_left, bisect_right
from statistics import median
from decimal import Decimal, ROUND_HALF_UP
from guanlan_domain.observer_movers.limits import finite
from guanlan_domain.observer_movers.limit_rules import market_band; from guanlan_domain.observer_movers.limit_rules import valid_bar; from guanlan_domain.observer_movers.limit_rules import daily_shape

MODEL_VERSION = 'guanlan_leader_observation_v0_6'
# Evidence weights sum to 100; board contribution itself can reach 50.
WEIGHTS = {'price_today': 15, 'price_previous4': 25, 'boards': 20,
           'daily_shape': 20, 'volume_adjustment': 20}
NON_LIMIT = {'not_limit', 'unrestricted', 'suspended', 'not_listed'}


def display_number(value):
    return str(Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def clip(value):
    return max(0.0, min(1.0, value))


def daily_return(row):
    if not row or not valid_bar(row):
        return None
    value = row.get('pct_chg')
    derived = row['close'] / row['pre_close'] - 1.0
    if finite(value):
        result = value / 100.0
        if abs(result - derived) > .003:
            return None
    else:
        result = derived
    return derived if finite(derived) and derived > -1 else None


def compounded(rows):
    changes = [daily_return(r) for r in rows]
    if any(x is None for x in changes):
        return None
    value = 1.0
    for change in changes:
        value *= 1 + change
    return value - 1


def drawdown(rows):
    """D-10 close is 1; D-9 through D add exactly ten returns."""
    if len(rows) != 11 or not rows[0] or not valid_bar(rows[0]):
        return None
    value = peak = 1.0
    worst = 0.0
    for row in rows[1:]:
        change = daily_return(row)
        if change is None:
            return None
        value *= 1 + change
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak)
    return worst


def midrank(values):
    ordered = sorted(v for v in values.values() if v is not None and finite(v))
    if len(ordered) < 100:
        return {}
    count = len(ordered)
    return {key: (bisect_left(ordered, val) + (bisect_right(ordered, val) - bisect_left(ordered, val)) / 2) / count
            for key, val in values.items() if val is not None and finite(val)}


def _amount(row):
    if not row or not finite(row.get('amount_thousand_cny')) or row['amount_thousand_cny'] < 0:
        return None
    return row['amount_thousand_cny'] * 1000.0


def _volume_data(byday, sessions):
    dates = sessions[-6:-1] if len(sessions) >= 6 else []
    row = byday.get(sessions[-1])
    volumes = [byday.get(d, {}).get('vol_lot') for d in dates]
    unknown = [d for d, v in zip(dates, volumes) if not finite(v) or v <= 0]
    reason = None
    if len(dates) != 5 or unknown:
        reason = 'five_positive_session_volumes_unavailable'
    elif not row or not finite(row.get('vol_lot')) or row['vol_lot'] <= 0:
        reason = 'current_positive_volume_unavailable'
    else:
        # Known corporate-action hints invalidate comparability; never adjust
        # share volume with a price adjustment factor or skip calendar days.
        for day in dates + [sessions[-1]]:
            item = byday.get(day, {})
            if item.get('volume_comparable') is False or item.get('split_ratio') not in (None, 1, 1.0):
                reason = 'share_volume_comparability_unverified'
                break
            pos = sessions.index(day)
            previous = byday.get(sessions[pos-1]) if pos else None
            if previous and finite(previous.get('close')) and finite(item.get('pre_close')):
                if abs(Decimal(str(previous['close']))-Decimal(str(item['pre_close']))) > Decimal('.01'):
                    reason = 'reference_price_adjustment_volume_comparability_unverified'
                    break
    baseline = sum(Decimal(str(v)) for v in volumes)/5 if len(volumes) == 5 and not unknown else None
    ratio = Decimal(str(row['vol_lot']))/baseline if not reason and baseline else None
    return {'ratio': float(ratio) if ratio is not None else None,
            'baseline_volume_lot': float(baseline) if baseline is not None else None,
            'volume_lot': row.get('vol_lot') if row else None, 'baseline_dates': dates,
            'unknown_dates': unknown, 'unknown_reasons': [reason] if reason else []}


def volume_adjustment(ratio, state):
    sealed = state == 'confirmed_up'
    non_limit = state in NON_LIMIT
    if ratio is None:
        low, high = -20.0, 0.0 if non_limit else 20.0
    elif ratio > 1:
        low = high = -float(min(Decimal(20), Decimal(20)*(Decimal(str(ratio))-1)))
    elif ratio == 1 or non_limit:
        low = high = 0.0
    else:
        high = float(min(Decimal(20), Decimal(40)*(1-Decimal(str(ratio)))))
        low = high if sealed else 0.0
    return {'points': low if low == high else None, 'min': low, 'max': high}


def _features(code, series, sessions, facts):
    index = len(sessions) - 1
    day = sessions[index]
    byday = series.get(code, {})
    row = byday.get(day)
    today = daily_return(row)
    previous4 = compounded([byday.get(d) for d in sessions[index-4:index]]) if index >= 4 else None
    prior_amounts = [_amount(byday.get(d)) for d in sessions[index-20:index]] if index >= 20 else []
    basis = median(prior_amounts) if len(prior_amounts) == 20 and all(x is not None for x in prior_amounts) else None
    amount = _amount(row)
    if basis is not None and basis <= 0:
        basis = None
    last10 = sessions[index-9:index+1] if index >= 9 else []
    event_states = [facts.get((code, date), {'status': 'unknown', 'reason': 'missing_session_quote'}) for date in last10]
    known = sum(x['status'] == 'confirmed_up' for x in event_states)
    unknown = sum(x['status'] in ('unknown', 'conflict') for x in event_states)
    if len(last10) < 10:
        unknown += 10 - len(last10)
    band, _ = market_band(code, day, (row or {}).get('name'))
    current_fact = facts.get((code, day), {})
    current_state = current_fact.get('status')
    cohort = (current_fact.get('band') or (str(band) if band is not None else None)) if current_state in ('confirmed_up', 'not_limit') else None
    return {'today': today, 'previous4': previous4, 'amount': amount, 'basis': basis,
            'volume': _volume_data(byday, sessions), 'current_fact': current_fact,
            'board_known': known, 'board_unknown': unknown,
            'cohort': cohort, 'day': day, 'code': code, 'row': row, 'event_states': event_states,
            'event_dates': last10}


def _rank_feature(features, name):
    cohorts = {}
    for code, feat in features.items():
        if feat['cohort']:
            cohorts.setdefault(feat['cohort'], {})[code] = feat.get(name)
    output = {}
    for values in cohorts.values():
        output.update(midrank(values))
    return output


def _n_m_tag(feat):
    dates, states = feat['event_dates'], feat['event_states']
    known = feat['board_known']
    unknown = feat['board_unknown']
    detail = {'window': 10, 'known_boards': known, 'unknown_days': unknown,
              'min_boards': known, 'max_boards': min(10, known + unknown),
              'days': [{'date': d, **s} for d, s in zip(dates, states)]}
    if unknown or known < 2 or len(dates) != 10:
        return None, detail
    if states[-1]['status'] == 'confirmed_up':
        first = next(i for i, item in enumerate(states) if item['status'] == 'confirmed_up')
        n = len(states) - first
        return f'{n}天{known}板', {**detail, 'start': dates[first], 'end': dates[-1], 'n': n, 'm': known}
    return f'近10天{known}板', {**detail, 'start': dates[0], 'end': dates[-1], 'n': 10, 'm': known}


def score_all(series, sessions, facts, *, extra_codes=()):
    """Market-wide stock-date score; grouping and filtering happen afterwards."""
    if not sessions:
        return {}, {}
    day = sessions[-1]
    codes = sorted({code for code, byday in series.items() if day in byday} | set(extra_codes))
    features = {code: _features(code, series, sessions, facts) for code in codes}
    price_today = _rank_feature(features, 'today')
    price_prev = _rank_feature(features, 'previous4')
    sample_counts = {}
    for feat in features.values():
        cohort = feat['cohort']
        if cohort:
            counts = sample_counts.setdefault(cohort, {'today': 0, 'previous4': 0})
            for name in counts:
                counts[name] += feat[name] is not None
    scores = {}
    for code, feat in features.items():
        pieces = {}
        def put(name, value, *, low_bound=0.0, high_bound=None, **details):
            pieces[name] = {'weight': WEIGHTS[name], 'points': value,
                            'min': value if value is not None else low_bound,
                            'max': value if value is not None else (WEIGHTS[name] if high_bound is None else high_bound),
                            **details}
            pieces[name]['points_display'] = display_number(value) if value is not None else None
        for name, ranks, feature, weight in [('price_today', price_today, 'today', 15),
                                             ('price_previous4', price_prev, 'previous4', 25)]:
            value = weight*ranks[code] if code in ranks else None
            put(name, value, percentile=ranks.get(code), return_value=feat[feature],
                sample_count=sample_counts.get(feat['cohort'], {}).get(feature, 0),
                dates=[day] if feature == 'today' else sessions[-5:-1],
                formula=f'{weight}*midrank_percentile',
                unknown_reasons=[] if value is not None else ['return_or_comparison_cohort_unavailable'])
        boards_low = 5 * feat['board_known']
        boards_high = 5 * min(10, feat['board_known'] + feat['board_unknown'])
        put('boards', boards_low if boards_low == boards_high else None, low_bound=boards_low, high_bound=boards_high,
            known_boards=feat['board_known'], unknown_days=feat['board_unknown'], formula='5*M10',
            unknown_reasons=['unknown_daily_limit_states'] if feat['board_unknown'] else [])
        shape = daily_shape(feat['row'], feat['current_fact'])
        put('daily_shape', shape['points'], low_bound=shape['min'], high_bound=shape['max'],
            **{k:v for k,v in shape.items() if k not in ('points', 'min', 'max')})
        state = feat['current_fact'].get('status', 'unknown')
        volume = feat['volume']
        q = volume_adjustment(volume['ratio'], state)
        put('volume_adjustment', q['points'], low_bound=q['min'], high_bound=q['max'],
            **volume, formula='limit&L<1:+min(20,40*(1-L)); L>1:-min(20,20*(L-1)); otherwise:0')
        lower = sum(v['min'] for v in pieces.values())
        upper = sum(v['max'] for v in pieces.values())
        if state not in NON_LIMIT | {'confirmed_up'}:
            # Board count, shape and contraction reward share the same current
            # limit fact; combine compatible sealed/nonsealed branches.
            pmin = pieces['price_today']['min'] + pieces['price_previous4']['min']
            pmax = pieces['price_today']['max'] + pieces['price_previous4']['max']
            prior_unknown = max(0, feat['board_unknown']-1)
            branch = []
            for sealed in (False, True):
                candidate_state = 'confirmed_up' if sealed else 'not_limit'
                f = daily_shape(feat['row'], {**feat['current_fact'], 'status':candidate_state})
                v = volume_adjustment(volume['ratio'], candidate_state)
                bmin = 5*(feat['board_known']+int(sealed))
                bmax = 5*min(10, feat['board_known']+prior_unknown+int(sealed))
                branch.append((pmin+bmin+f['min']+v['min'], pmax+bmax+f['max']+v['max']))
            lower, upper = min(v[0] for v in branch), max(v[1] for v in branch)
        lower, upper = max(0.0, lower), max(0.0, upper)
        covered = sum(v['weight'] for v in pieces.values() if v['points'] is not None)
        tag, board_detail = _n_m_tag(feat)
        value = lower if abs(upper-lower) < 1e-9 else None
        scores[code] = {'code': code, 'date': day, 'model_version': MODEL_VERSION,
                        'score': value, 'score_display': display_number(value) if value is not None else None,
                        'lower_display': display_number(lower), 'upper_display': display_number(upper),
                        'score_scale': {'min':0, 'theoretical_max':130},
                        'lower': lower, 'upper': upper, 'covered_weight': covered,
                        'components': pieces, 'cohort': feat['cohort'],
                        'n_m_label': tag, 'boards': board_detail, 'today_return': feat['today'],
                        'prior4_return': feat['previous4'], 'amount_yuan': feat['amount'],
                        'amount_baseline_yuan': feat['basis']}
    return scores, features


def group_summaries(catalog, scores, features, sessions):
    """Full current membership is the denominator; no anomaly-only percentages."""
    groups = [(g['kind'], g['id'], g['name'], [m['code'] for m in g.get('members', [])]) for g in catalog['groups']]
    groups += [('theme', t['id'], t['name'], [m['code'] for m in t.get('members', [])]) for t in catalog['themes']]
    def five_day(feat):
        return (1 + feat['previous4']) * (1 + feat['today']) - 1 if feat['previous4'] is not None and feat['today'] is not None else None
    market_values = [five_day(v) for v in features.values()]
    market5 = median(v for v in market_values if v is not None) if any(v is not None for v in market_values) else None
    result = {}
    for kind, group_id, name, members in groups:
        unique = sorted(set(members))
        found = [features[c] for c in unique if c in features]
        valid = [x for x in found if x['today'] is not None]
        rated = sorted((scores[c]['score'], c) for c in unique if c in scores and scores[c]['score'] is not None)
        rated.reverse()
        ranks = {}
        last_value = None
        current_rank = 0
        for pos, (value, code) in enumerate(rated, 1):
            if value != last_value:
                current_rank = pos
                last_value = value
            ranks[code] = current_rank
        complete = len(rated)
        coverage = complete / len(unique) if unique else 0
        hot = None
        group5 = [five_day(x) for x in valid if five_day(x) is not None]
        group5_median = median(group5) if unique and len(group5) / len(unique) >= .95 else None
        if len(unique) >= 10 and len(valid) / len(unique) >= .95:
            up = sum(x['today'] > 0 for x in valid) / len(valid)
            movers = sum(x['today'] > .07 for x in valid) / len(valid)
            heats = [x['amount'] / x['basis'] for x in valid if x['amount'] is not None and x['basis']]
            if group5_median is not None and len(heats) / len(unique) >= .95 and market5 is not None:
                hot = 30 * up + 25 * clip(movers / .15) + 25 * clip((group5_median - market5 + .02) / .05) + 20 * clip((median(heats) - 1) / 2)
        qualified = [(value, code) for value, code in rated if value >= 75 and scores[code]['today_return'] is not None and scores[code]['today_return'] >= 0 and code in features and five_day(features[code]) is not None and five_day(features[code]) > 0]
        candidate = qualified[0][1] if len(unique) >= 10 and coverage >= .95 and qualified else None
        close_candidate = bool(candidate and len(qualified) > 1 and qualified[0][0] - qualified[1][0] < 5)
        result[f'{kind}:{group_id}'] = {'kind': kind, 'id': group_id, 'name': name,
                                        'member_count': len(unique), 'valid_member_count': len(valid),
                                        'scored_member_count': complete, 'score_coverage': coverage,
                                        'heat': hot, 'ranks': ranks, 'candidate': candidate,
                                        'candidate_close': close_candidate,
                                        'relative5': {code: five_day(features[code]) - group5_median
                                                      for code in unique if group5_median is not None
                                                      and code in features and five_day(features[code]) is not None}}
    return result
