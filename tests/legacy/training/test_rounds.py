"""Fixed-length sampling and buy-stay/Space-advance acceptance for v0.3."""
import tempfile,unittest,uuid,json
from guanlan_data.repositories.training.data import Data
from guanlan_app.features.training.service import Service
from tests.legacy.training.test_service import fixture; from tests.legacy.training.test_service import writable

class RoundTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=tempfile.gettempdir(),prefix='training-rounds-')
        self.data=fixture(self.folder.name);self.service=Service(self.data)
    def tearDown(self):self.service.close();self.folder.cleanup()
    def call(self,op,params,request=None):return self.service.invoke(op,params,request or str(uuid.uuid4()))
    def act(self,s,action,request=None):return self.call('training.act',dict(id=s['id'],expected_revision=s['revision'],expected_phase=s['phase'],action=action),request)
    def test_latest_start_includes_exactly_150_trading_days(self):
        days=self.data.dates('600001.SH')[120:]
        chosen=Data(self.data.paths).choose(2,{'length':150,'from':days[-150],'to':days[-150]})
        self.assertEqual(chosen['start'],days[-150]);self.assertEqual(chosen['end'],days[-1])
        with self.assertRaisesRegex(ValueError,'完整150'):Data(self.data.paths).choose(2,{'length':150,'from':days[-149],'to':days[-149]})
    def test_stock_hole_or_short_tail_cannot_start_fixed_length(self):
        with writable(self.data.paths['raw']) as c:c.execute("DELETE FROM equity_daily_raw WHERE trade_date='2024-01-03'")
        with self.assertRaisesRegex(ValueError,'完整150'):Data(self.data.paths).choose(1,{'length':150,'from':'2024-01-02','to':'2024-01-02'})
    def test_future_factor_missing_cannot_start_and_delisted_history_is_retained(self):
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_master SET list_status='D',delist_date='2024-09-01'")
        chosen=Data(self.data.paths).choose(1,{'length':60,'from':'2024-01-02','to':'2024-01-02'})
        self.assertEqual(chosen['start'],'2024-01-02')
        with writable(self.data.paths['raw']) as c:c.execute("DELETE FROM equity_adj_factor WHERE trade_date='2024-01-03'")
        with self.assertRaisesRegex(ValueError,'完整60'):Data(self.data.paths).choose(1,{'length':60,'from':'2024-01-02','to':'2024-01-02'})
    def test_buy_stays_open_zero_cost_retry_then_four_rounds(self):
        s=self.call('training.start',{'length':150})
        self.assertEqual((s['round'],s['total_rounds']), (1,300))
        self.assertEqual((s['settings']['fee_bps'],s['settings']['slippage_bps']),(0,0))
        params=dict(id=s['id'],expected_revision=s['revision'],expected_phase=s['phase'],action='BUY');request=str(uuid.uuid4())
        s=self.call('training.act',params,request)
        self.assertEqual((s['day'],s['phase'],s['round'],s['revision']),(1,'OPEN',1,1))
        self.assertEqual(set(s['quotes']),{'open'});self.assertEqual(s['last_receipt']['fee'],0)
        self.assertAlmostEqual(s['account']['cost'],s['last_receipt']['price']);self.assertEqual(s['account']['fees'],0)
        self.assertEqual(self.call('training.act',params,request),s)
        s=self.act(s,'HOLD');self.assertEqual((s['day'],s['phase'],s['round']),(1,'CLOSE',2));self.assertIn('T+1',s['reasons']['SELL'])
        s=self.act(s,'HOLD');self.assertEqual((s['day'],s['phase'],s['round']),(2,'OPEN',3));self.assertGreater(s['account']['unlocked'],0)
        s=self.act(s,'HOLD');self.assertEqual((s['day'],s['phase'],s['round']),(2,'CLOSE',4))
    def test_sale_stays_current_round(self):
        s=self.act(self.call('training.start',{'length':60}),'BUY')
        s=self.act(self.act(s,'HOLD'),'HOLD');s=self.act(s,'SELL')
        self.assertEqual((s['day'],s['phase'],s['round']),(2,'OPEN',3));self.assertEqual(s['account']['units'],0)
        self.assertEqual(s['last_receipt']['fee'],0)
    def test_final_close_trade_waits_for_space_to_complete(self):
        s=self.call('training.start',{'length':60})
        for _ in range(119):s=self.act(s,'HOLD')
        self.assertEqual((s['day'],s['phase'],s['round']),(60,'CLOSE',120))
        s=self.act(s,'BUY');self.assertEqual(s['status'],'active');self.assertEqual(s['round'],120)
        s=self.act(s,'HOLD');self.assertEqual(s['status'],'completed');self.assertEqual(s['rounds_completed'],120)
        self.assertGreater(s['account']['units'],0)
    def test_legacy_committed_buy_retry_preserves_cursor_fees_and_new_manual_flow(self):
        s=self.call('training.start',{'length':60,'fee_bps':5})
        request=str(uuid.uuid4());params=dict(id=s['id'],expected_revision=0,expected_phase='OPEN',action='BUY')
        s=self.call('training.act',params,request)
        # Model an old v0.2 committed BUY that already advanced to CLOSE.
        with self.service.store.transaction() as c:
            stored=self.service.store.get(c,s['id']);stored['phase']='CLOSE'
            for key in ('flow_version','window_end','sampling_version'):stored.pop(key,None)
            receipt=json.loads(c.execute('SELECT receipt FROM actions WHERE session_id=?',(s['id'],)).fetchone()[0])
            for key in ('flow_version','advanced','round'):receipt.pop(key,None)
            c.execute('UPDATE actions SET receipt=? WHERE session_id=?',(json.dumps(receipt),s['id']))
            self.service.store.save(c,stored)
        s=self.call('training.act',params,request)
        self.assertEqual((s['phase'],s['round'],s['revision']),('CLOSE',2,1))
        self.assertEqual(s['settings']['fee_bps'],5);self.assertEqual(len(s['actions']),1)
        self.assertGreater(s['account']['fees'],0);fees=s['account']['fees']
        s=self.act(s,'HOLD');self.assertEqual((s['day'],s['phase'],s['round']),(2,'OPEN',3))
        s=self.act(s,'SELL');self.assertEqual((s['day'],s['phase'],s['round']),(2,'OPEN',3))
        self.assertGreater(s['account']['fees'],fees);self.assertEqual(s['settings']['fee_bps'],5)

if __name__=='__main__':unittest.main()
