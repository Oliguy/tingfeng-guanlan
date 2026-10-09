import unittest
from guanlan_domain.observer_math.chart_metrics import enrich


class HoverMetrics(unittest.TestCase):
    def rows(self):
        return [{'trade_date':f'2026-09-{21+i:02d}','close':10+i,'volume':100*(i+1)} for i in range(6)]

    def test_ratio_uses_prior_five_and_change_previous_close(self):
        rows=self.rows();got=enrich(rows)[-1]
        self.assertAlmostEqual(got['volume_ratio'],2)
        self.assertAlmostEqual(got['change_pct'],15/14-1)
        self.assertNotIn('change_pct',rows[-1])

    def test_first_day_source_change_survives_adjustment(self):
        row=enrich([{'trade_date':'2026-09-30','close':65.09,'volume':100,'change_pct':2.065944}])[0]
        self.assertEqual(row['change_pct'],2.065944)
        self.assertIsNone(row['volume_ratio'])

    def test_calendar_gap_is_not_a_zero_volume_halt(self):
        class Calendar:
            def sessions(self,a,b):return [f'2026-09-{21+i:02d}' for i in range(7)]
        rows=self.rows();rows[-1]['trade_date']='2026-09-27'
        self.assertIsNone(enrich(rows,calendar=Calendar())[-1]['volume_ratio'])
        self.assertAlmostEqual(enrich(rows,calendar=Calendar(),suspensions={'2026-09-26':True})[-1]['volume_ratio'],600/(200+300+400+500+0)*5)

    def test_missing_week_and_zero_average_do_not_fake_ratio(self):
        rows=[{'trade_date':f'2026-0{i+1}-01','close':10,'volume':0} for i in range(6)]
        got=enrich(rows,'weekly')[-1]
        self.assertIsNone(got['volume_ratio'])
        self.assertIn('零',got['volume_ratio_reason'])
        rows[2]['status']='missing';rows[2]['volume']=100
        self.assertIsNone(enrich(rows,'weekly')[-1]['volume_ratio'])

    def test_industry_ratio_preserves_total_volume(self):
        rows=self.rows()
        for i,r in enumerate(rows):r['mean_volume']=10*(i+1);r['volume']=1000+i
        got=enrich(rows,volume_field='mean_volume')[-1]
        self.assertEqual(got['volume'],1005);self.assertEqual(got['volume_ratio'],2)

