import unittest,random
from guanlan_domain.observer_math.indicators import OverlayAccumulator; from guanlan_domain.observer_math.indicators import apply_overlays; from guanlan_domain.observer_math.indicators import aggregate_weekly; from guanlan_domain.observer_math.indicators import week_key; from guanlan_domain.observer_math.indicators import _finite; from guanlan_domain.observer_math.indicators import chart_rows
from datetime import date,timedelta
from guanlan_domain.observer_calendar.core import Calendar

class StreamingTests(unittest.TestCase):
    def test_exact_original_arithmetic_with_holes_and_long_history(self):
        rng=random.Random(714);values=[None if i%41==0 else rng.uniform(1,4000) for i in range(2000)]
        rows=[{'trade_date':str(i),'close':v} for i,v in enumerate(values)]
        expected=apply_overlays(rows,{})
        stream=OverlayAccumulator()
        for row in expected:
            actual=stream.append(row['close'])
            for key,value in actual.items():self.assertEqual(value,row[key],(row['trade_date'],key))
        self.assertEqual(len(stream.closes),114)
    def test_weekly_optimization_matches_original_daily_loop(self):
        first=date(2020,1,1);rows=[];days={}
        for i in range(850):
            d=first+timedelta(days=i);day=d.isoformat();days[day]=int(d.weekday()<5)
            if days[day] and i!=420:rows.append({'trade_date':day,'open':10+i/7,'high':20+i/7,'low':1+i/7,'close':12+i/7,'volume':1})
        cal=Calendar(days)
        for end in [200,220,len(rows)-1,len(rows)]:
            known=rows[:end];weeks=aggregate_weekly(known,cal);prior={r['week_start']:[p.get('close') for p in weeks[max(0,i-29):i]] for i,r in enumerate(weeks)};gaps={r['week_start'] for r in weeks if r.get('calendar_gap')};line={}
            for r in known:
                key=week_key(r['trade_date']);previous=prior.get(key,[]);v=r['close']
                line[r['trade_date']]=(sum(previous)+v)/30 if key not in gaps and len(previous)==29 and all(_finite(x) for x in previous) and _finite(v) else None
            self.assertEqual(chart_rows(known,'daily',cal),apply_overlays(known,line))
            self.assertEqual(chart_rows(known,'weekly',cal),apply_overlays(weeks,line))

if __name__=='__main__':unittest.main()
