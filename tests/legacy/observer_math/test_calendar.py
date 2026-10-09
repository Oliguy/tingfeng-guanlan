import unittest
from datetime import date,timedelta
from guanlan_domain.observer_calendar.core import Calendar
from guanlan_domain.observer_math.indicators import aggregate_weekly; from guanlan_domain.observer_math.indicators import chart_rows
from guanlan_domain.observer_math.signals import strength; from guanlan_domain.observer_math.signals import price_strength; from guanlan_domain.observer_math.signals import recent_pair

def sample(*,closed_week=30,missing_week=None,partial=False):
    days={};rows=[];start=date(2025,1,6)
    for i in range(35*7):
        d=start+timedelta(days=i);week=i//7
        opened=d.weekday()<5 and week!=closed_week
        days[d.isoformat()]=int(opened)
        if opened and week!=missing_week:
            close=100 if week<29 else 101+week-29
            rows.append(dict(trade_date=d.isoformat(),open=close,high=close,low=close,close=close,volume=1,amount=1))
    if partial:rows=rows[:-3]
    return Calendar(days),rows

class TradingWeeks(unittest.TestCase):
    def test_closed_week_is_skipped_and_gold_retained(self):
        cal,rows=sample()
        weeks=chart_rows(rows,'weekly',cal)
        self.assertEqual(len(weeks),34)
        self.assertTrue(recent_pair(weeks[29:31],cal)['two_weeks_above'])
        self.assertEqual(price_strength(rows,cal)['consecutive_above_weeks'],5)
        self.assertTrue(price_strength(rows,cal)['two_weeks_above'])
        self.assertEqual(price_strength(rows,cal)['strength_score'],5)
    def test_missing_open_week_is_not_compressed(self):
        cal,rows=sample(missing_week=31)
        weeks=chart_rows(rows,'weekly',cal)
        self.assertEqual(len(weeks),34)
        missing=[r for r in weeks if r.get('calendar_gap')]
        self.assertEqual(len(missing),1)
        self.assertIsNone(missing[0]['close'])
        self.assertIsNone(price_strength(rows,cal)['above_now'])
        self.assertFalse(price_strength(rows,cal)['two_weeks_above'])
    def test_current_partial_week_and_chart_match(self):
        cal,rows=sample(partial=True)
        day=chart_rows(rows,'daily',cal)[-1]
        week=chart_rows(rows,'weekly',cal)[-1]
        self.assertEqual(day['thirty_week_ma'],week['thirty_week_ma'])
        self.assertEqual(price_strength(rows,cal)['above_now'],day['close']>day['thirty_week_ma'])
        self.assertEqual(week['trade_date'],rows[-1]['trade_date'])
    def test_short_week_uses_actual_final_close(self):
        cal,rows=sample();key=rows[-3]['trade_date']
        days=dict(cal.days)
        for r in rows[-2:]:days[r['trade_date']]=0
        cal=Calendar(days);rows=rows[:-2]
        self.assertEqual(chart_rows(rows,'weekly',cal)[-1]['trade_date'],key)
    def test_preheated_weekly_values_preserved_and_equal_is_below(self):
        cal,rows=sample();weeks=chart_rows(rows,'weekly',cal)
        self.assertEqual(strength(weeks[-4:],cal)['consecutive_above_weeks'],4)
        rows=[{**r,'close':100,'open':100,'high':100,'low':100} for r in rows]
        self.assertEqual(price_strength(rows,cal)['consecutive_above_weeks'],0)
        self.assertIsNone(price_strength(rows[:50],cal)['above_now'])
    def test_unexplained_missing_day_does_not_count_as_halt(self):
        cal,rows=sample();del rows[-2]
        self.assertIsNone(price_strength(rows,cal)['above_now'])

if __name__=='__main__':unittest.main()
