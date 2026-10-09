"""Synthetic checks; production sources are never copied or modified."""
import unittest
from datetime import date, timedelta

from guanlan_domain.observer_movers.leader_score import score_all; from guanlan_domain.observer_movers.leader_score import group_summaries
from guanlan_domain.observer_movers.limit_rules import decide


def bar(code, day, close=10.0):
    return {'ts_code': code, 'trade_date': day, 'open': close, 'high': close,
            'low': close, 'close': close, 'pre_close': close,
            'pct_chg': 0.0, 'vol_lot': 1000, 'amount_thousand_cny': 1000,
            'name': '测试', 'list_date': '2020-01-01'}


class LeaderTests(unittest.TestCase):
    def sample(self, event_positions=(5, 7, 9), unknown_positions=()):
        dates = [f'2026-09-{n:02d}' for n in range(1, 31)]
        dates += ['2026-10-01']
        series, facts = {}, {}
        for n in range(120):
            code = f'{n:06d}.SZ'
            series[code] = {day: bar(code, day, 10+n/100) for day in dates}
            for position, day in enumerate(dates[-10:]):
                state = 'confirmed_up' if n == 0 and position in event_positions else 'not_limit'
                if n == 0 and position in unknown_positions:
                    state = 'unknown'
                facts[(code, day)] = {'status': state, 'reason': None, 'sources': []}
            previous = 10+n/100
            for day in dates:
                state = facts.get((code,day),{}).get('status','not_limit')
                close = previous*1.1 if state == 'confirmed_up' else previous
                row = series[code][day]
                row.update(pre_close=previous,open=close,high=close,low=close,close=close,
                           pct_chg=(close/previous-1)*100)
                if (code,day) in facts:
                    facts[(code,day)].update(up_limit=round(previous*1.1,2),band='0.10')
                previous=close
        return dates, series, facts

    def test_n_m_label_and_board_accumulation(self):
        dates, series, facts = self.sample()
        scores, _ = score_all(series, dates, facts)
        self.assertEqual(scores['000000.SZ']['n_m_label'], '5天3板')
        self.assertEqual(scores['000000.SZ']['components']['boards']['points'], 15)
        self.assertEqual(scores['000000.SZ']['boards']['known_boards'], 3)
        dates, series, facts = self.sample((5, 6, 7, 8, 9))
        scores, _ = score_all(series, dates, facts)
        self.assertEqual(scores['000000.SZ']['n_m_label'], '5天5板')
        self.assertEqual(scores['000000.SZ']['components']['boards']['points'], 25)

    def test_unknown_does_not_create_precise_tag_or_inflated_score(self):
        dates, series, facts = self.sample(unknown_positions=(6,))
        score = score_all(series, dates, facts)[0]['000000.SZ']
        self.assertIsNone(score['n_m_label'])
        self.assertIsNone(score['score'])
        self.assertEqual(score['components']['boards']['points'], None)
        self.assertEqual(score['upper'] - score['lower'], 5)

    def test_group_references_do_not_change_stock_score(self):
        dates, series, facts = self.sample()
        scores, features = score_all(series, dates, facts)
        codes = list(series)
        catalog = {'groups': [{'kind': 'industry', 'id': 'one', 'name': '甲',
                               'members': [{'code': c} for c in codes]},
                              {'kind': 'industry', 'id': 'two', 'name': '乙',
                               'members': [{'code': c} for c in codes[:110]]}],
                   'themes': []}
        groups = group_summaries(catalog, scores, features, dates)
        self.assertIn('000000.SZ', groups['industry:one']['ranks'])
        self.assertIn('000000.SZ', groups['industry:two']['ranks'])
        self.assertEqual(scores['000000.SZ']['score'], score_all(series, dates, facts)[0]['000000.SZ']['score'])

    def test_dated_source_and_rule_conflict(self):
        row = bar('000001.SZ', '2026-09-30', 11)
        row['pre_close'] = 10
        row['pct_chg'] = 10
        support = {'status': 'available', 'up_limit': 11.01, 'down_limit': 9,
                   'request_id': 'synthetic'}
        result = decide(row, list_date='2020-01-01', prior_quote_days=30, support=support)
        self.assertEqual(result['status'], 'conflict')
        row['close'] = row['open'] = row['high'] = row['low'] = 10.5
        support = {'status': 'available', 'up_limit': 10.5, 'down_limit': 9.5,
                   'request_id': 'dated-five-percent'}
        result = decide(row, list_date='2020-01-01', prior_quote_days=30, support=support)
        self.assertEqual((result['status'], result['band']), ('confirmed_up', '0.05'))
        row['vol_lot'] = -1
        result = decide(row, list_date='2020-01-01', prior_quote_days=30)
        self.assertEqual(result['status'], 'unknown')

    def test_touch_is_not_close_and_ipo_window_is_unrestricted(self):
        row = bar('000001.SZ', '2026-09-30', 10.5)
        row['pre_close'] = 10
        row['high'] = 11
        result = decide(row, list_date='2020-01-01', prior_quote_days=30)
        self.assertEqual(result['status'], 'not_limit')
        row['close'] = row['high'] = 11
        session_index = {'2026-09-28': 0, '2026-09-29': 1, '2026-09-30': 2}
        result = decide(row, list_date='2026-09-30', prior_quote_days=0,
                        session_index=session_index)
        self.assertEqual(result['status'], 'unrestricted')

    def test_historical_status_and_new_listing_without_evidence_stay_unknown(self):
        row = bar('000001.SZ', '2026-07-03', 11)
        row['pre_close'] = 10
        self.assertEqual(decide(row, list_date='2020-01-01', prior_quote_days=30)['status'], 'unknown')
        row['trade_date'] = '2026-09-30'
        self.assertEqual(decide(row, prior_quote_days=2)['status'], 'unknown')

    def test_bj_tick_is_date_rule_not_approximate_change(self):
        row = bar('830001.BJ', '2026-09-30', 13.01)
        row['pre_close'] = 10.01
        result = decide(row, list_date='2020-01-01', prior_quote_days=30)
        self.assertEqual((result['status'], result['up_limit']), ('confirmed_up', 13.01))

    def test_exchange_session_axis_skips_weekend_and_holiday(self):
        cursor = date(2026, 9, 1)
        sessions = []
        while cursor <= date(2026, 9, 30):
            if cursor.weekday() < 5 and cursor.isoformat() != '2026-09-25':
                sessions.append(cursor.isoformat())
            cursor += timedelta(days=1)
        code = '000001.SZ'
        series = {code: {day: bar(code, day) for day in sessions}}
        facts = {(code, day): {'status': 'not_limit', 'reason': None, 'sources': []}
                 for day in sessions}
        for day in (sessions[-5], sessions[-3], sessions[-1]):
            facts[(code, day)]['status'] = 'confirmed_up'
        score = score_all(series, sessions, facts)[0][code]
        self.assertEqual(score['n_m_label'], '5天3板')
        missing = sessions[-4]
        series[code].pop(missing)
        facts.pop((code, missing))
        score = score_all(series, sessions, facts)[0][code]
        self.assertIsNone(score['n_m_label'])
        self.assertEqual(score['boards']['unknown_days'], 1)


if __name__ == '__main__':
    unittest.main()
