import unittest
from guanlan_domain.training.account import initial; from guanlan_domain.training.account import execute; from guanlan_domain.training.account import numbers; from guanlan_domain.training.account import reasons
from guanlan_domain.training.rules import point_gate; from guanlan_domain.training.rules import limits
def point(raw, normalized=None,rule=None):
    rule=rule or limits(code='600001.SH',date='20240102',list_date='20000101',pre_close=10,status={'is_st':0})
    price=raw*10 if normalized is None else normalized
    return {'price':price,'gate':point_gate(raw,rule),'raw_price':raw,'multiplier':str(price/raw),'rule':rule}
class AccountTests(unittest.TestCase):
    def test_t1_hand_calculated_fee_and_hold(self):
        a,r=execute(initial(),'BUY',point(10),1,fee_bps=5)
        self.assertAlmostEqual(numbers(a,100,1)['units'],100000/100.05)
        self.assertEqual(numbers(a,100,1)['unlocked'],0)
        with self.assertRaisesRegex(ValueError,r'T\+1'):execute(a,'SELL',point(10),1)
        held,receipt=execute(a,'HOLD',point(10.2),1);self.assertEqual(a,held);self.assertEqual(receipt['fee'],0)
        a,r=execute(a,'SELL',point(10),2,fee_bps=5);self.assertAlmostEqual(float(a['cash']),100000*.9995/1.0005);self.assertEqual(a['units'],'0')
    def test_current_point_both_directions(self):
        with self.assertRaisesRegex(ValueError,'涨停'):execute(initial(),'BUY',point(11),1)
        a,_=execute(initial(),'BUY',point(10.9),1)
        with self.assertRaisesRegex(ValueError,'跌停'):execute(a,'SELL',point(9),2)
        self.assertEqual(reasons(a,point(11),2)['SELL'],'')
        execute(a,'SELL',point(11),2)
        execute(initial(),'BUY',point(9),1)
    def test_price_domains_and_unknown(self):
        # raw half, factor double: same nominal account/benchmark price, no 50% loss.
        a,_=execute(initial(),'BUY',point(10,100),1)
        self.assertAlmostEqual(numbers(a,100,2)['nav'],100000)
        a,_=execute(a,'HOLD',point(5,100,limits(code='600001.SH',date='20240103',list_date='20000101',pre_close=5,status={'is_st':0})),2)
        unknown={'kind':'unknown'}
        with self.assertRaisesRegex(ValueError,'规则'):execute(initial(),'BUY',point(10,100,unknown),1)
        self.assertEqual(execute(initial(),'HOLD',point(10,100,unknown),1)[0],initial())
    def test_single_position_rejections(self):
        with self.assertRaisesRegex(ValueError,'无持仓'):execute(initial(),'SELL',point(10),1)
        a,_=execute(initial(),'BUY',point(10),1)
        with self.assertRaisesRegex(ValueError,'已有持仓'):execute(a,'BUY',point(10),2)
    def test_slippage_rounds_raw_ticks_and_stays_within_limits(self):
        a,buy=execute(initial(),'BUY',point(10.99),1,slippage_bps=100)
        self.assertAlmostEqual(buy['price'],110)
        a,sell=execute(a,'SELL',point(9.01),2,slippage_bps=100)
        self.assertAlmostEqual(sell['price'],90)
        _,tick=execute(initial(),'BUY',point(10),1,slippage_bps=5)
        self.assertAlmostEqual(tick['price'],100.1)
        # An explicit unrestricted point has tick rounding but no fabricated cap.
        _,free=execute(initial(),'BUY',point(10.99,rule={'kind':'unlimited'}),1,slippage_bps=100)
        self.assertAlmostEqual(free['price'],111)
if __name__=='__main__':unittest.main()
