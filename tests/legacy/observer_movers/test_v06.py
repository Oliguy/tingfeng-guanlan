"""v0.6 regression scenarios use synthetic data only."""
import unittest
from guanlan_domain.observer_movers.limit_rules import daily_shape; from guanlan_domain.observer_movers.limit_rules import decide
from guanlan_domain.observer_movers.leader_score import volume_adjustment; from guanlan_domain.observer_movers.leader_score import score_all; from guanlan_domain.observer_movers.leader_score import display_number
import tests.legacy.observer_movers.test_leader as fixture


def quote(open=10.8, low=10.8, close=11, base=10, vol=800):
    return {'ts_code':'000001.SZ','trade_date':'2026-09-30','open':open,'high':max(open,close),
            'low':low,'close':close,'pre_close':base,'vol_lot':vol,
            'amount_thousand_cny':1000,'pct_chg':(close/base-1)*100}


class DailyShapeTests(unittest.TestCase):
    def shape(self, row, limit=11):
        return daily_shape(row, {'status':'confirmed_up','up_limit':limit})

    def test_one_price_requires_all_prices_and_real_volume(self):
        self.assertEqual(self.shape(quote(11,11))['points'],20)
        self.assertEqual(self.shape(quote(11,10.2))['points'],10)
        self.assertIsNone(self.shape(quote(11,11,vol=0))['points'])
        self.assertEqual(decide(quote(11,11,vol=0),list_date='2020-01-01',prior_quote_days=30)['status'],'unknown')

    def test_body_and_lower_shadow_are_daily_only(self):
        for opening,low,expected in [(10.9,10.9,16.2),(10.8,10.8,14.4),
                                     (10.5,10.5,9),(10,10,0),(10.9,10.2,9.2)]:
            self.assertAlmostEqual(self.shape(quote(opening,low))['points'],expected)
        self.assertAlmostEqual(self.shape(quote(11.6,11.6,12),12)['points'],14.4)

    def test_small_entity_without_closing_limit_is_zero(self):
        row=quote(10.8,10.8,10.8)
        self.assertEqual(daily_shape(row,{'status':'not_limit'})['points'],0)

    def test_missing_prices_and_source_conflicts_are_not_zero(self):
        self.assertIsNone(daily_shape(None,{'status':'confirmed_up'})['points'])
        self.assertIsNone(daily_shape(quote(),{'status':'confirmed_up','up_limit':None})['points'])
        row=quote();row['high']=11.02
        fact=decide(row,list_date='2020-01-01',prior_quote_days=30,
                    support={'status':'available','up_limit':11,'down_limit':9})
        self.assertEqual(fact['status'],'conflict')


class ScoreV06Tests(unittest.TestCase):
    def test_volume_rewards_and_penalties(self):
        for ratio,points in [(.5,20),(.6,16),(.8,8),(.9,4),(1,0),(1.2,-4),(1.5,-10),(2,-20),(3,-20)]:
            self.assertAlmostEqual(volume_adjustment(ratio,'confirmed_up')['points'],points)
        self.assertEqual(volume_adjustment(.8,'not_limit')['points'],0)
        self.assertEqual(volume_adjustment(1.5,'not_limit')['points'],-10)
        self.assertEqual(volume_adjustment(None,'not_limit')['max'],0)

    def test_more_boards_and_total_above_100(self):
        for count in (4,5,6):
            days,series,facts=fixture.LeaderTests().sample(tuple(range(10-count,10)))
            series['000000.SZ'][days[-1]]['vol_lot']=500
            scored=score_all(series,days,facts)[0]['000000.SZ']
            self.assertEqual(scored['components']['boards']['points'],count*5)
            self.assertEqual(scored['components']['daily_shape']['points'],20)
            self.assertEqual(scored['components']['volume_adjustment']['points'],20)
            self.assertGreater(scored['score'],100 if count==6 else 0)
            self.assertNotIn('volume_heat',scored['components'])

    def test_missing_volume_day_is_not_skipped(self):
        days,series,facts=fixture.LeaderTests().sample()
        series['000000.SZ'].pop(days[-3]);facts.pop(('000000.SZ',days[-3]))
        scored=score_all(series,days,facts)[0]['000000.SZ']
        self.assertIsNone(scored['components']['volume_adjustment']['points'])
        self.assertEqual(scored['components']['volume_adjustment']['baseline_dates'],days[-6:-1])

    def test_missing_current_quote_remains_unknown(self):
        days,series,facts=fixture.LeaderTests().sample()
        series['000000.SZ'].pop(days[-1]);facts.pop(('000000.SZ',days[-1]))
        scored=score_all(series,days,facts,extra_codes=['000000.SZ'])[0]['000000.SZ']
        self.assertIsNone(scored['score'])
        self.assertEqual(scored['today_return'],None)

    def test_known_adjustment_cannot_create_fake_contraction(self):
        days,series,facts=fixture.LeaderTests().sample()
        series['000000.SZ'][days[-1]]['volume_comparable']=False
        scored=score_all(series,days,facts)[0]['000000.SZ']
        self.assertIsNone(scored['components']['volume_adjustment']['points'])
        self.assertEqual(display_number(10.25),'10.3')


if __name__=='__main__': unittest.main()
