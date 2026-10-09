"""Carry capital through stock switches using synthetic data and an own Store."""
import json,tempfile,unittest,uuid
from decimal import Decimal
from guanlan_domain.training.account import initial; from guanlan_domain.training.account import numbers
from guanlan_app.features.training.service import Service
from tests.legacy.training.test_service import fixture; from tests.legacy.training.test_service import writable


def two_stocks(root):
    data=fixture(root)
    with writable(data.paths['raw']) as c:
        c.execute("UPDATE equity_daily_raw SET open=10.8,close=10.8 WHERE trade_date='2024-01-03'")
        c.execute("INSERT INTO equity_master SELECT '600002.SH','第二个合成样本',market,list_date,delist_date,list_status FROM equity_master")
        c.execute("INSERT INTO equity_daily_raw SELECT '600002.SH',trade_date,open,high,low,close,pre_close,vol_lot FROM equity_daily_raw")
        c.execute("INSERT INTO equity_adj_factor SELECT '600002.SH',trade_date,adj_factor FROM equity_adj_factor")
    with writable(data.paths['status']) as c:
        c.execute("INSERT INTO equity_status_daily SELECT exchange,'600002',trade_date,is_st,is_delisted FROM equity_status_daily")
        c.execute("INSERT INTO namechange_source SELECT '600002.SH','第二个合成样本',start_date,end_date,change_reason FROM namechange_source")
    data.choose=lambda seed,options:{'code':'600002.SH' if options.get('_exclude_code')=='600001.SH' else '600001.SH','start':'2024-01-02'}
    return data


class CapitalTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=tempfile.gettempdir(),prefix='training-capital-')
        self.data=two_stocks(self.folder.name);self.service=Service(self.data)
        self.state=self.call('training.start',{'length':60,'response_mode':'delta'})
    def tearDown(self):self.service.close();self.folder.cleanup()
    def call(self,op,params,request=None):return self.service.invoke(op,params,request or str(uuid.uuid4()))
    def act(self,action):
        self.state=self.call('training.act',dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase'],action=action,response_mode='delta'))
        return self.state
    def stop(self):
        self.state=self.call('training.finish',dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase'],abandon=True,response_mode='delta'))
        return self.state
    def next(self,request=None):
        self.state=self.call('training.start',dict(self.state['settings'],after_id=self.state['id'],response_mode='delta'),request)
        return self.state
    def gain(self):
        for action in ('BUY','HOLD','HOLD','SELL'):self.act(action)
        self.assertAlmostEqual(self.state['account']['nav'],108000)

    def test_empty_position_switch_keeps_money_and_cumulative_gain(self):
        self.gain();ended=self.stop();new=self.next()
        self.assertEqual(new['account']['cash'],108000)
        self.assertEqual(new['account']['units'],0)
        self.assertAlmostEqual(new['account']['return'],0)
        self.assertAlmostEqual(new['account']['cumulative_return'],8)
        self.assertEqual(new['record']['initial_capital'],108000)
        self.assertEqual(new['record']['profit'],0)
        self.assertAlmostEqual(new['record']['cumulative_profit'],8000)
        restored=self.call('training.state',{'id':ended['id']})
        self.assertEqual(restored['account'],ended['account'])

    def test_frozen_position_is_valued_without_fabricating_sale(self):
        self.act('BUY');self.act('HOLD');ended=self.stop()
        self.assertEqual(ended['account']['nav'],105000)
        self.assertGreater(ended['account']['frozen'],0)
        new=self.next()
        self.assertEqual(new['account']['cash'],105000)
        self.assertEqual(new['account']['frozen'],0)
        self.assertEqual(new['account']['cost'],0)
        self.assertEqual(new['record']['carry_method'],'last_known_nav')
        rows=self.call('training.history',{'id':ended['id']})['items']
        self.assertEqual([r['action'] for r in rows],['BUY','HOLD'])
        self.assertEqual(ended['record']['sells'],0)

    def test_losses_and_multi_stock_returns_use_correct_baselines(self):
        self.gain();self.stop();self.next()
        self.act('BUY');self.act('HOLD')
        self.assertAlmostEqual(self.state['account']['return'],5)
        self.assertAlmostEqual(self.state['account']['cumulative_return'],13.4)
        ended=self.stop();self.assertAlmostEqual(ended['record']['profit'],5400)
        self.assertAlmostEqual(ended['review']['return_percent'],5)
        self.assertAlmostEqual(ended['review']['cumulative_return'],13.4)
        new=self.next();self.assertAlmostEqual(new['account']['cash'],113400)
        self.assertEqual(new['account']['return'],0)
        self.assertAlmostEqual(new['account']['cumulative_return'],13.4)
        self.act('BUY');self.act('HOLD');self.act('HOLD');self.act('SELL')
        self.assertAlmostEqual(self.state['account']['return'],8)
        self.assertAlmostEqual(self.state['account']['cumulative_return'],22.472)

    def test_switch_after_loss_and_explicit_new_training_resets(self):
        with writable(self.data.paths['raw']) as c:
            c.execute("UPDATE equity_daily_raw SET open=9.2,close=9.2 WHERE trade_date='2024-01-03'")
        for action in ('BUY','HOLD','HOLD','SELL'):self.act(action)
        ended=self.stop();self.assertAlmostEqual(ended['account']['nav'],92000)
        new=self.next();self.assertAlmostEqual(new['account']['cash'],92000)
        self.assertEqual(new['record']['drawdown_percent'],0)
        reset=self.call('training.start',{'length':60})
        self.assertEqual(reset['account']['nav'],100000)
        self.assertEqual(reset['account']['cumulative_return'],0)
        self.assertIsNone(reset['record']['previous_session'])

    def test_decimal_capital_fees_retry_and_reopen_are_preserved(self):
        with self.service.store.transaction() as c:
            s=self.service.store.get(c,self.state['id']);s['settings']['fee_bps']=10;self.service.store.save(c,s)
        for action in ('BUY','HOLD','HOLD','SELL'):self.act(action)
        self.stop();params=dict(self.state['settings'],after_id=self.state['id'],response_mode='delta');request=str(uuid.uuid4())
        with self.service.store.transaction() as c:
            prior=self.service.store.get(c,self.state['id']);expected=Decimal(prior['account']['cash'])
        new=self.call('training.start',params,request);again=self.call('training.start',params,request)
        self.assertEqual(new,again)
        with self.service.store.transaction() as c:
            self.assertEqual(Decimal(self.service.store.get(c,new['id'])['account']['cash']),expected)
            self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],2)
        self.assertEqual(new['account']['fees'],0)
        self.assertAlmostEqual(new['account']['cumulative_fees'],float(Decimal(prior['account']['fees'])))
        self.service.close();self.service=Service(self.data)
        restored=self.call('training.state',{'id':new['id'],'response_mode':'delta'})
        self.assertEqual(restored['account'],new['account'])

    def test_legacy_account_without_baselines_stays_readable(self):
        old={'cash':'98000','units':'0','buy_day':None,'cost':'0','fees':'0'}
        a=numbers(old,100,1)
        self.assertEqual(a['return'],-2)
        self.assertEqual(a['cumulative_return'],-2)
        self.assertEqual(a['initial_capital'],100000)

    def test_review_and_replay_keep_carried_baseline(self):
        self.gain();self.stop();self.next();self.act('BUY');self.act('HOLD');self.stop()
        replay=self.call('training.replay',dict(id=self.state['id'],revision=1,response_mode='delta'))
        self.assertEqual(replay['account']['initial_capital'],108000)
        # Replaying the OPEN decision must not use the original later CLOSE.
        self.assertAlmostEqual(replay['account']['return'],0)
        self.assertAlmostEqual(replay['account']['cumulative_return'],8)
        self.assertEqual(replay['kind'],'replay')

    def test_failed_next_save_rolls_back_and_retry_keeps_exact_capital(self):
        self.gain();ended=self.stop();params=dict(ended['settings'],after_id=ended['id']);request=str(uuid.uuid4())
        with self.service.store.transaction() as c:parent=c.execute('SELECT payload FROM sessions WHERE id=?',(ended['id'],)).fetchone()[0]
        save=self.service.store.save
        def fail(*args):raise OSError('synthetic training save failure')
        self.service.store.save=fail
        try:
            with self.assertRaises(OSError):self.call('training.start',params,request)
        finally:self.service.store.save=save
        with self.service.store.transaction() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],1)
            self.assertIsNone(c.execute('SELECT id FROM requests WHERE id=?',(request,)).fetchone())
            self.assertEqual(c.execute('SELECT payload FROM sessions WHERE id=?',(ended['id'],)).fetchone()[0],parent)
        new=self.call('training.start',params,request);self.assertEqual(new['account']['cash'],108000)

    def test_legacy_switch_keeps_parent_payload_unchanged(self):
        self.gain();ended=self.stop()
        with self.service.store.transaction() as c:
            s=self.service.store.get(c,ended['id'])
            for key in ('initial_capital','origin_capital','prior_fees'):s['account'].pop(key)
            self.service.store.save(c,s);parent=c.execute('SELECT payload FROM sessions WHERE id=?',(ended['id'],)).fetchone()[0]
        new=self.next();self.assertEqual(new['account']['cash'],108000);self.assertAlmostEqual(new['account']['cumulative_return'],8)
        with self.service.store.transaction() as c:
            self.assertEqual(c.execute('SELECT payload FROM sessions WHERE id=?',(ended['id'],)).fetchone()[0],parent)

    def test_depleted_capital_stays_zero_instead_of_resetting(self):
        ended=self.stop()
        with self.service.store.transaction() as c:
            s=self.service.store.get(c,ended['id']);s['account']['cash']='0';self.service.store.save(c,s)
        new=self.next();self.assertEqual(new['account']['nav'],0);self.assertEqual(new['account']['return'],0)
        self.assertEqual(new['account']['cumulative_return'],-100);self.assertIn('不足',new['reasons']['BUY'])


if __name__=='__main__':unittest.main()
