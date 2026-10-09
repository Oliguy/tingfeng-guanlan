"""Portfolio baskets, group series and leader selection over explicit inputs."""
from __future__ import annotations
from datetime import date, timedelta
from statistics import median
from typing import Any, Mapping
from guanlan_domain.guanlan_backend.market_etf.domain.primitives import canonical; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import daily_flow; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import RULE_VERSION; from guanlan_domain.guanlan_backend.market_etf.domain.primitives import LEADER_MIN_HISTORY_WEEKS

def monthly_basket(month: str, members: list[dict[str, Any]], prices: Mapping[tuple[str, str], dict], days: list[str], listing_dates: Mapping[str, str | None]) -> dict[str, Any]:
    first = date.fromisoformat(month + '-01')
    previous_end = first - timedelta(days=1)
    previous_start = previous_end.replace(day=1).isoformat()
    month_days = [day for day in days if day.startswith(month)]
    effective = next((day for day in month_days if any((r['effective_from'] <= day <= (r.get('effective_to') or '9999-12-31') and (not listing_dates.get(r['etf_code']) or listing_dates[r['etf_code']] <= day) for r in members))), None)
    cohort = sorted({r['etf_code'] for r in members if effective and r['effective_from'] <= effective <= (r.get('effective_to') or '9999-12-31') and (not listing_dates.get(r['etf_code']) or listing_dates[r['etf_code']] <= effective)})
    basis_days = [day for day in days if previous_start <= day <= previous_end.isoformat()]
    totals, missing = ({}, [])
    for code in cohort:
        records = [prices.get((code, day)) for day in basis_days]
        if not basis_days or any((row is None or row.get('amount') is None for row in records)):
            missing.append(code)
        totals[code] = sum((float(row['amount']) for row in records if row and row.get('amount') is not None))
    grand = sum(totals.values())
    weights = {code: total / grand for code, total in totals.items()} if grand and (not missing) else {}
    payload = {'month': month, 'effective_from': effective, 'basis_month': previous_start[:7], 'basis_membership_date': effective, 'weights': weights, 'members': cohort, 'missing_members': missing, 'status': 'PASS' if weights else 'UNAVAILABLE', 'membership_basis': 'record_effective'}
    return payload

def calculate_group(*, group_id, start, end, members, raw, listing_dates, calendar, observations, leaders, revision, timestamp):
    """Return immutable-by-convention output tuples; never read/write an adapter.

    Quality tuple position 14 contains a basket month. The repository binds it
    to its persisted revision at publication, inside one input-fenced transaction.
    """
    first = min([start, *(r['effective_from'] for r in members)])
    calendar_set = set(calendar)
    previous_days = {day: calendar[index - 1] if index else None for index, day in enumerate(calendar)}
    dates = sorted({day for day in calendar if first <= day <= end} | {r['trade_date'] for r in raw if first <= r['trade_date'] <= end})
    prices = {(r['etf_code'], r['trade_date']): r for r in raw}
    baskets = {}
    output = []
    quality = []
    previous_index, continuity_lost, started = (1000.0, False, False)
    amounts: list[float] = []
    for day in dates:
        active_rows = [r for r in members if r['effective_from'] <= day <= (r.get('effective_to') or '9999-12-31') and (not listing_dates.get(r['etf_code']) or listing_dates[r['etf_code']] <= day)]
        active = sorted({r['etf_code'] for r in active_rows})
        if not active:
            continue
        reasons = []
        membership_status = 'PASS' if len(active) == len(active_rows) else 'FAIL'
        if membership_status != 'PASS':
            reasons.append('duplicate_member_intervals')
        covered = [prices[code, day] for code in active if prices.get((code, day), {}).get('amount') is not None]
        amount = sum((float(r['amount']) for r in covered)) if covered else None
        volumes = [prices.get((code, day), {}).get('volume') for code in active]
        volume = sum((float(value) for value in volumes if value is not None)) if any((value is not None for value in volumes)) else None
        quote_status = 'PASS' if len(covered) == len(active) else 'PARTIAL' if covered else 'MISSING'
        if any((value is None for value in volumes)):
            reasons.append('missing_member_volumes')
        relative = amount / median(amounts[-20:]) if quote_status == 'PASS' and amount is not None and (len(amounts) >= 20) and median(amounts[-20:]) else None
        if quote_status == 'PASS' and amount is not None:
            amounts.append(amount)
        if quote_status != 'PASS':
            reasons.append('missing_member_quotes')
        month = day[:7]
        if month not in baskets:
            baskets[month] = monthly_basket(month, members, prices, calendar, listing_dates)
        basket = baskets[month]
        weights = basket['weights']
        value, return_coverage = (0.0, 0.0)
        prior_day = previous_days.get(day)
        return_status = 'PASS' if weights and day in calendar_set else 'UNAVAILABLE'
        for code, weight in weights.items():
            if weight <= 0:
                continue
            current = prices.get((code, day), {})
            previous = prices.get((code, prior_day), {})
            close, reference = (current.get('close'), previous.get('close'))
            if current.get('prev_close') is not None and reference is not None:
                reference = current['prev_close']
            if close is None or reference is None or float(reference) <= 0:
                return_status = 'PARTIAL'
                continue
            value += weight * (float(close) / float(reference) - 1.0)
            return_coverage += weight
        group_return = value if return_status == 'PASS' and membership_status == 'PASS' else None
        if not weights:
            reasons.append('monthly_weights_unavailable')
        if group_return is None:
            if started:
                continuity_lost = True
            synthetic = None
            reasons.append('return_inputs_incomplete')
        elif continuity_lost:
            synthetic = None
            reasons.append('prior_continuity_gap')
        else:
            previous_index *= 1.0 + group_return
            synthetic, started = (previous_index, True)
        flow_values = []
        for code in active:
            flow, reason = daily_flow(observations.get(code, {}).get(day), observations.get(code, {}).get(prior_day))
            if flow is not None:
                flow_values.append(flow)
            else:
                reasons.append(reason)
        flow_count = len(flow_values)
        subtotal = sum(flow_values) if flow_values else None
        flow_status = 'PASS' if flow_count == len(active) else 'PARTIAL' if flow_count else 'UNAVAILABLE'
        leader = leaders.get(month)
        leader_code = leader if leader in active else None
        leader_status = 'PASS' if leader_code else 'UNAVAILABLE'
        if not leader_code:
            reasons.append('monthly_leader_unavailable')
        day_calendar = 'PASS' if day in calendar_set else 'MISSING'
        history_status = 'PASS' if synthetic is not None else 'MISSING'
        overall = 'PASS' if all((s == 'PASS' for s in (day_calendar, membership_status, quote_status, return_status, flow_status, leader_status, history_status))) else 'PARTIAL'
        output.append((day, group_id, leader_code, volume, amount, relative, subtotal if flow_status == 'PASS' else None, group_return, synthetic, len(active), len(covered), len(covered) / len(active), overall, RULE_VERSION, timestamp))
        quality.append((day, group_id, revision, day_calendar, membership_status, quote_status, return_status, leader_status, flow_status, history_status, flow_count, len(active), subtotal, return_coverage, month, canonical(sorted(set(reasons))), 'record_effective'))
    return {'daily': output, 'quality': quality, 'baskets': baskets}

def select_leader(rows, expected_days, previous_month, group_id):
    rows = [dict(row) for row in rows]
    for row in rows:
        row['coverage_ratio'] = row['covered_days'] / expected_days
    eligible = [row for row in rows if row['coverage_ratio'] >= 1.0]
    if not eligible:
        raise RuntimeError(f'{previous_month} has no eligible {group_id} ETF leader candidate')
    for row in eligible:
        row['history_start_date'] = row.get('inception_date') or row.get('listing_date') or row.get('first_trade_date')
        row['meets_60_week_history'] = int(row.get('history_weeks') or 0) >= LEADER_MIN_HISTORY_WEEKS
    mature = [row for row in eligible if row['meets_60_week_history']]
    if mature:
        selection_mode = 'absolute_amount_among_60_week_candidates'
        candidate_pool = mature
        candidate_pool.sort(key=lambda row: (-float(row['monthly_amount'] or 0), -int(row['covered_days']), -float(row['average_amount_20d'] or 0), str(row['etf_code'])))
    else:
        selection_mode = 'longest_history_fallback'
        candidate_pool = eligible
        candidate_pool.sort(key=lambda row: (str(row.get('history_start_date') or '9999-12-31'), -int(row.get('history_weeks') or 0), -float(row['monthly_amount'] or 0), str(row['etf_code'])))
    winner = candidate_pool[0]
    return (winner, eligible, mature, rows, selection_mode)
