"""Single-session records and deliberate stop/next, on synthetic sources only."""
import json,tempfile,unittest,uuid
from guanlan_data.repositories.training.data import Data
from guanlan_app.features.training.service import Service
from tests.legacy.training.test_service import fixture; from tests.legacy.training.test_service import writable

class RecordTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=tempfile.gettempdir(),prefix='training-records-')
        self.data=fixture(self.folder.name)
        with writable(self.data.paths['raw']) as c:
            c.execute("INSERT INTO equity_master SELECT '600002.SH','另一个合成样本',market,list_date,delist_date,list_status FROM equity_master")
            c.execute("INSERT INTO equity_daily_raw SELECT '600002.SH',trade_date,open,high,low,close,pre_close,vol_lot FROM equity_daily_raw")
            c.execute("INSERT INTO equity_adj_factor SELECT '600002.SH',trade_date,adj_factor FROM equity_adj_factor")
        with writable(self.data.paths['status']) as c:
            c.execute("INSERT INTO equity_status_daily SELECT exchange,'600002',trade_date,is_st,is_delisted FROM equity_status_daily")
            c.execute("INSERT INTO namechange_source SELECT '600002.SH','另一个合成样本',start_date,end_date,change_reason FROM namechange_source")
        self.service=Service(self.data);self.state=self.call('training.start',{'length':60,'response_mode':'delta'})
    def tearDown(self):self.service.close();self.folder.cleanup()
    def call(self,op,params,request=None):return self.service.invoke(op,params,request or str(uuid.uuid4()))
    def stop(self,request=None):return self.call('training.finish',dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase'],abandon=True,response_mode='delta'),request)
    def actual_sampler(self):self.data.choose=Data.choose.__get__(self.data,Data)
    def test_skip_is_saved_and_next_excludes_previous_stock(self):
        self.assertEqual(self.state['record']['duration_seconds'],0);self.assertEqual(self.state['record']['started_at'],self.state['record']['last_activity_at'])
        self.state=self.stop();self.assertEqual(self.state['status'],'abandoned');self.assertEqual(self.state['record']['end_reason'],'skipped')
        self.assertIn('reveal',self.state);self.assertFalse(self.state['review']['eligible']);self.assertTrue(self.state['record']['ended_at'])
        self.actual_sampler();new=self.call('training.start',dict(self.state['settings'],after_id=self.state['id'],response_mode='delta'))
        with self.service.store.transaction() as c:
            self.assertNotEqual(self.service.store.get(c,new['id'])['code'],self.service.store.get(c,self.state['id'])['code'])
        self.assertEqual((new['day'],new['phase'],new['round']),(1,'OPEN',1));self.assertNotIn('reveal',new)
        self.assertEqual(new['record']['previous_session'],self.state['id'])
    def test_interrupt_position_keeps_cost_frozen_shares_and_receipts(self):
        self.state=self.call('training.act',dict(id=self.state['id'],expected_revision=0,expected_phase='OPEN',action='BUY',response_mode='delta'))
        before=self.state['account'];self.state=self.stop()
        self.assertEqual(self.state['account']['units'],before['units']);self.assertEqual(self.state['account']['cost'],before['cost']);self.assertEqual(self.state['account']['frozen'],before['frozen'])
        self.assertEqual(self.state['record']['end_reason'],'interrupted');self.assertEqual(self.state['record']['buys'],1);self.assertEqual(self.state['record']['sells'],0)
        self.assertEqual(self.state['action_count'],1);self.assertTrue(self.state['record']['started_at']);self.assertTrue(self.state['record']['ended_at'])
        event=self.call('training.history',{'id':self.state['id']})['items'][0]
        self.assertEqual(event['account_after']['frozen'],before['frozen']);self.assertEqual(event['account_after']['cash'],0);self.assertTrue(event['recorded_at'])
        self.call('training.review',dict(id=self.state['id'],revision=0))
    def test_same_stop_and_start_requests_never_create_extra_games(self):
        req=str(uuid.uuid4());params=dict(id=self.state['id'],expected_revision=0,expected_phase='OPEN',abandon=True,response_mode='delta')
        ended=self.call('training.finish',params,req);again=self.call('training.finish',params,req)
        self.assertEqual(ended,again);self.actual_sampler();params=dict(ended['settings'],after_id=ended['id'],response_mode='delta');req=str(uuid.uuid4())
        first=self.call('training.start',params,req);second=self.call('training.start',params,req);self.assertEqual(first,second)
        with self.service.store.transaction() as c:self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],2)
    def test_failed_next_preserves_ended_game_and_can_retry_same_request(self):
        self.state=self.stop();self.actual_sampler();original=self.data.pool();self.data._pool=[m for m in original if m['ts_code']=='600001.SH']
        params=dict(self.state['settings'],after_id=self.state['id'],response_mode='delta');req=str(uuid.uuid4())
        with self.assertRaises(ValueError):self.call('training.start',params,req)
        restored=self.call('training.state',{'id':self.state['id'],'response_mode':'delta'});self.assertEqual(restored['record']['ended_at'],self.state['record']['ended_at']);self.assertEqual(restored['status'],'abandoned')
        self.data._pool=original;new=self.call('training.start',params,req);self.assertEqual(new['status'],'active')
    def test_next_cannot_close_another_active_game(self):
        with self.assertRaisesRegex(ValueError,'先结束'):self.call('training.start',dict(self.state['settings'],after_id=self.state['id']))
        self.assertEqual(self.call('training.state',{'id':self.state['id']})['status'],'active')
    def test_source_pause_can_interrupt_without_losing_held_account(self):
        self.state=self.call('training.act',dict(id=self.state['id'],expected_revision=0,expected_phase='OPEN',action='BUY'))
        before=self.state['account']
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_adj_factor SET adj_factor=2 WHERE ts_code='600001.SH' AND trade_date='2023-01-02'")
        self.state=self.call('training.state',{'id':self.state['id']});self.assertEqual(self.state['status'],'paused')
        self.state=self.stop();self.assertEqual(self.state['status'],'abandoned');self.assertEqual(self.state['account']['nav'],before['nav']);self.assertEqual(self.state['account']['units'],before['units']);self.assertTrue(self.state['review']['source_invalid'])
    def test_old_receipt_is_not_rewritten_and_note_is_in_full_history(self):
        self.state=self.call('training.act',dict(id=self.state['id'],expected_revision=0,expected_phase='OPEN',action='BUY'))
        with self.service.store.transaction() as c:
            row=c.execute('SELECT receipt FROM actions').fetchone()[0];event=json.loads(row);event.pop('_account_after',None);event.pop('recorded_at',None);old=json.dumps(event)
            c.execute('UPDATE actions SET receipt=?',(old,))
        self.state=self.stop();self.call('training.note',dict(id=self.state['id'],revision=0,note='复盘备注',mistake=True))
        event=self.call('training.history',{'id':self.state['id']})['items'][0];self.assertEqual(event['note'],'复盘备注');self.assertTrue(event['mistake']);self.assertEqual(event['account_after']['cash'],0)
        with self.service.store.transaction() as c:self.assertEqual(c.execute('SELECT receipt FROM actions').fetchone()[0],old)
    def test_nonzero_fee_drawdown_matches_record_and_review(self):
        with self.service.store.transaction() as c:
            s=self.service.store.get(c,self.state['id']);s['settings']['fee_bps']=10;self.service.store.save(c,s)
        for action in ('BUY','HOLD','HOLD','SELL'):
            self.state=self.call('training.act',dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase'],action=action))
        self.state=self.stop();self.assertEqual(self.state['record']['drawdown_percent'],self.state['review']['drawdown_percent']);self.assertEqual(self.state['record']['buys'],1);self.assertEqual(self.state['record']['sells'],1);self.assertEqual(self.state['record']['holds'],2)
        self.assertAlmostEqual(self.state['record']['profit'],self.state['record']['realized_profit']+self.state['record']['unrealized_profit'])

if __name__=='__main__':unittest.main()
