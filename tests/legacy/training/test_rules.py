import unittest
from guanlan_domain.training.rules import limits; from guanlan_domain.training.rules import point_gate; from guanlan_domain.training.rules import cents

class RulesTests(unittest.TestCase):
    def rule(self, **kw):
        args=dict(code='600001.SH',date='20240102',pre_close=10,list_date='20000101',status={'is_st':0,'is_delisted':0})
        args.update(kw);return limits(**args)
    def test_date_st_and_board(self):
        for code,date,st,up in [('600001.SH','20260703',1,1050),('600001.SH','20260706',1,1100),('300001.SZ','20200821',0,1100),('300001.SZ','20200824',0,1200),('688001.SH','20200102',1,1200)]:
            with self.subTest(code=code,date=date):self.assertEqual(self.rule(code=code,date=date,status={'is_st':st})['up'],up)
    def test_tick_half_up_and_low_price(self):
        self.assertEqual(self.rule(pre_close=10.05)['up'],1106)
        self.assertEqual(self.rule(pre_close=.01)['up'],2)
        self.assertIsNone(cents(10.001))
        self.assertEqual(point_gate(11,self.rule())['buy'],'涨停 · 禁止买入')
        self.assertEqual(point_gate(10.99,self.rule())['buy'],'')
        self.assertEqual(point_gate(9,self.rule())['sell'],'跌停 · 禁止卖出')
    def test_unknown_special_direct_conflicts(self):
        for kw in [dict(status=None),dict(list_date=''),dict(special=True),dict(listing_day=1),dict(code='830001.BJ')]:
            self.assertEqual(self.rule(**kw)['kind'],'unknown')
        self.assertEqual(self.rule(direct={'date':'20240102','up':11,'down':9,'status':'available'},status=None)['origin'],'direct')
        self.assertEqual(self.rule(direct={'date':'20240102','up':999999.999,'down':9,'status':'available'})['kind'],'unknown')
        self.assertEqual(self.rule(explicit_unlimited=True)['kind'],'unlimited')
        self.assertEqual(point_gate(999,self.rule(explicit_unlimited=True))['buy'],'')
        self.assertFalse(point_gate(11.01,self.rule())['valid'])

if __name__=='__main__': unittest.main()
