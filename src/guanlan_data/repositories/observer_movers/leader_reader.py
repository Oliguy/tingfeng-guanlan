"""Bounded read-only projection for leader observation and N-day/M-board facts."""
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
import copy

from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import readonly
from guanlan_data.repositories.observer_calendar import load as load_calendar; from guanlan_data.repositories.observer_calendar import source_stamp as calendar_stamp
from guanlan_data.repositories.observer_movers.catalog import stamp
from guanlan_domain.observer_movers.limit_rules import decide
from guanlan_data.repositories.observer_movers.limits import STATES
from guanlan_domain.observer_movers.leader_score import MODEL_VERSION; from guanlan_domain.observer_movers.leader_score import group_summaries; from guanlan_domain.observer_movers.leader_score import score_all; from guanlan_domain.observer_movers.leader_score import NON_LIMIT


def _first_board(facts, code, sessions):
    current = facts.get((code, sessions[-1]), {}).get('status', 'unknown')
    previous = facts.get((code, sessions[-2]), {}).get('status', 'unknown') if len(sessions)>1 else 'unknown'
    if current in NON_LIMIT or previous == 'confirmed_up':
        return 'no'
    if current == 'confirmed_up' and previous == 'not_limit':
        return 'yes'
    return 'unknown'


def _five_day_window(reader, day_data, dates, calendar):
    """Use exchange sessions, retaining missing dates rather than shifting them."""
    merged = {}
    groups = {'industry':{}, 'subindustry':{}, 'theme':{}}
    missing, revisions = [], []
    for day in dates:
        try:
            data = day_data if day == day_data['date'] else reader.day(day)
        except ValueError as exc:
            if '尚无已采集日线' not in str(exc):
                raise
            missing.append(day)
            continue
        revisions.append([day, data['market_revision'], data['classification_revision'], data['data_revision']])
        for source in data['rows']:
            prior = merged.get(source['code'])
            event_dates = (prior['event_dates'] if prior else []) + [day]
            merged[source['code']] = {**copy.deepcopy(source), 'event_dates':event_dates,
                                       'event_date':day, 'score_date':day_data['date']}
        for axis, definitions in data['groups'].items():
            for group in definitions:
                saved = groups[axis].setdefault(group['id'], {**group, 'codes':set()})
                saved['codes'].update(group['codes'])
    rows = [merged[c] for c in sorted(merged)]
    stats = {'total':len(rows), 'up':sum(r['pct_chg']>7 for r in rows),
             'down':sum(r['pct_chg'] < -7 for r in rows),
             'limit_up':sum(r['limit']['status']=='confirmed_up' for r in rows),
             'limit_unknown':sum(r['limit']['status'] in ('unknown','conflict') for r in rows)}
    unknown_calendar = [d for d in calendar.dates if dates[0]<=d<=day_data['date'] and calendar.state(d) is None] if dates else []
    calendar_complete = bool(len(dates)==5 and calendar.covered(dates[0], day_data['date']))
    return {'dates':dates, 'complete':calendar_complete and not missing,
            'missing_dates':missing, 'calendar_complete':calendar_complete,
            'unknown_calendar_dates':unknown_calendar,
            'rows':rows, 'groups':{axis:[{**g,'codes':sorted(g['codes'])} for g in gs.values()] for axis,gs in groups.items()},
            'stats':stats, 'revision':digest([MODEL_VERSION,dates,revisions,calendar.revision]),
            'classification_revision':day_data['classification_revision'],
            'theme_catalog':day_data['theme_catalog']}


def _price_support(path, start, end):
    if not Path(path).exists():
        return {}
    with readonly(path) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='movers_limit_prices'").fetchone():
            return {}
        return {(r['ts_code'], r['trade_date']): dict(r) for r in connection.execute(
            'SELECT trade_date,ts_code,up_limit,down_limit,status,request_id '
            'FROM movers_limit_prices WHERE trade_date BETWEEN ? AND ?', (start, end))}


def _positive_events(path, start, end):
    if not Path(path).exists():
        return set()
    with readonly(path) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_limit_up'").fetchone():
            return set()
        return {(r['instrument_id'], f"{r['trade_date'][:4]}-{r['trade_date'][4:6]}-{r['trade_date'][6:8]}")
                for r in connection.execute('SELECT instrument_id,trade_date FROM daily_limit_up '
                                            'WHERE trade_date BETWEEN ? AND ? AND closed_at_limit=1',
                                            (start.replace('-', ''), end.replace('-', '')))}


def read_leader(reader, selected=None, *, expected_market=None, expected_classification=None):
    """Full local market is scored once; the HTTP response contains only anomaly rows."""
    day_data = reader.day(selected)
    day = day_data['date']
    if expected_market and expected_market != day_data['market_revision']:
        raise ValueError('当日行情已修订，请刷新异动列表')
    if expected_classification and expected_classification != day_data['classification_revision']:
        raise ValueError('分类目录已修订，请刷新异动列表')
    catalog = reader.catalog()
    stamps_before = [stamp(reader.market), stamp(reader.support), stamp(reader.kph),
                     calendar_stamp(reader.market)]
    with readonly(reader.market) as connection:
        calendar = load_calendar(reader.market, connection=connection)
        if calendar.state(day) != 1:
            raise ValueError('选定日期的交易日历不完整')
        sessions = list(calendar.open_dates)
        position = sessions.index(day)
        sessions = sessions[max(0, position - 20):position + 1]
        start = sessions[0]
        rows = connection.execute('SELECT r.ts_code,r.trade_date,r.open,r.high,r.low,r.close,r.pre_close,r.pct_chg,'
                                  'r.vol_lot,r.amount_thousand_cny,m.name,m.list_date '
                                  'FROM equity_daily_raw r LEFT JOIN equity_master m ON m.ts_code=r.ts_code '
                                  'WHERE r.trade_date BETWEEN ? AND ? ORDER BY r.trade_date,r.ts_code', (start, day))
        series = defaultdict(dict)
        for row in rows:
            record = dict(row)
            series[record['ts_code']][record['trade_date']] = record
        identities = {r['ts_code']:dict(r) for r in connection.execute('SELECT ts_code,name,list_date FROM equity_master')}
    window = _five_day_window(reader, day_data, sessions[-5:], calendar)
    display_codes = [r['code'] for r in window['rows']]
    fact_start = sessions[-20] if len(sessions) >= 20 else start
    support = _price_support(reader.support, fact_start, day)
    positive = _positive_events(reader.kph, fact_start, day)
    facts = {}
    session_index = {date: i for i, date in enumerate(sessions)}
    for code in set(series) | set(display_codes):
        byday = series.get(code, {})
        dates = sessions[-10:]
        ordered_dates = sorted(byday)
        for date in dates:
            row = byday.get(date)
            if not row:
                listing = str(identities.get(code, {}).get('list_date') or '').replace('-', '')
                before = bool(listing and date.replace('-','') < listing)
                facts[(code,date)] = {'status':'not_listed' if before else 'unknown',
                                     'reason':'before_listing' if before else 'missing_session_quote', 'sources':[]}
                continue
            prior = bisect_left(ordered_dates, date)
            facts[(code, date)] = decide(row, list_date=row.get('list_date'), prior_quote_days=prior,
                                         session_index=session_index,
                                         support=support.get((code, date)), positive=(code, date) in positive,
                                         name=row.get('name'))
    scores, features = score_all(series, sessions, facts, extra_codes=display_codes)
    if not calendar.covered(sessions[0], day):
        for value in scores.values():
            value.update(score=None,score_display=None,lower=0.0,upper=130.0,
                         lower_display='0.0',upper_display='130.0',covered_weight=0,
                         unknown_reasons=['calendar_window_incomplete'])
    for code, value in scores.items():
        value['first_board'] = _first_board(facts, code, sessions)
    summaries = group_summaries(catalog, scores, features, sessions)
    stamps_after = [stamp(reader.market), stamp(reader.support), stamp(reader.kph),
                    calendar_stamp(reader.market)]
    if stamps_before != stamps_after or catalog['revision'] != reader.catalog()['revision']:
        raise ValueError('评分来源读取期间变化，请重试本地评分')
    if day_data['market_revision'] != reader.day(day)['market_revision']:
        raise ValueError('当日行情读取期间变化，请重试本地评分')
    display_set = set(display_codes)
    for code in display_codes:
        if code not in scores:
            continue
        byday = series.get(code, {})
        ordered_dates = sorted(byday)
        for date in sessions[-20:-10]:
            if date not in byday:
                listing = str(identities.get(code, {}).get('list_date') or '').replace('-', '')
                if listing and date.replace('-','') < listing:
                    facts[(code,date)] = {'status':'not_listed','reason':'before_listing','sources':[]}
                continue
            row = byday[date]
            facts[(code, date)] = decide(row, list_date=row.get('list_date'),
                                         prior_quote_days=bisect_left(ordered_dates, date),
                                         session_index=session_index,
                                         support=support.get((code, date)),
                                         positive=(code, date) in positive, name=row.get('name'))
        detail = scores[code]['boards']
        window_counts = {}
        for size in (3, 5, 10, 20):
            count_window = sessions[-size:]
            states = [facts.get((code, date), {'status': 'unknown'})['status'] for date in count_window]
            known = states.count('confirmed_up')
            missing = states.count('unknown') + states.count('conflict') + max(0, size-len(count_window))
            window_counts[str(size)] = {'known': known, 'unknown': missing,
                                        'min': known, 'max': known+missing}
        detail['window_counts'] = window_counts
        detail['days'] = [{'date': date, **facts.get((code, date),
                          {'status': 'unknown', 'reason': 'missing_session_quote', 'sources': []})}
                          for date in sessions[-20:]]
    summarized_groups = {key: {k: ({code: number for code, number in v.items() if code in display_set}
                                   if k == 'relative5' else v)
                               for k, v in value.items() if k != 'ranks'}
                         for key, value in summaries.items()}
    ranks = {key: {code: value['ranks'].get(code) for code in display_codes if code in value['ranks']}
             for key, value in summaries.items()}
    for row in window['rows']:
        row['first_board'] = scores[row['code']]['first_board']
        event_fact = facts.get((row['code'], row['event_date']))
        if event_fact:
            row['limit'] = {**event_fact, 'label':STATES.get(event_fact['status'], '涨停待核验')}
    window['stats']['limit_up'] = sum(r['limit']['status']=='confirmed_up' for r in window['rows'])
    window['stats']['limit_unknown'] = sum(r['limit']['status'] in ('unknown','conflict') for r in window['rows'])
    return {'schema_version': MODEL_VERSION, 'date': day, 'window':window,
            'score_scale':{'min':0,'theoretical_max':130}, 'default_floor':35,
            'market_revision': day_data['market_revision'],
            'classification_revision': day_data['classification_revision'],
            'calendar_revision': calendar.revision,
            'source_revision': digest([stamps_after, calendar.revision, catalog['revision'], MODEL_VERSION]),
            'universe_size': len(features), 'scores': {code: scores[code] for code in display_codes if code in scores},
            'groups': summarized_groups, 'ranks': ranks,
            'basis': '本地全市场行情评分；历史分组采用当前分类，不代表当时分类或收益验证'}
