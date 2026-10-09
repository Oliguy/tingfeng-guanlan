from tests.legacy.training.test_service import ServiceTests; from tests.legacy.training.test_service import writable
from guanlan_app.features.training.service import Service
import json,uuid

class ReviewTests(ServiceTests):
    def finish(self):
        return self.call('training.finish',dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase']))
    def test_review_note_and_replay_locked_close(self):
        self.state=self.call('training.act',self.act_params('BUY'));self.state=self.call('training.act',self.act_params('HOLD'));self.state=self.call('training.act',self.act_params('HOLD'))
        self.state=self.finish();self.assertEqual(self.state['status'],'revealed');self.assertFalse(self.state['review']['eligible'])
        with self.service.store.transaction() as c:before=self.service.store.actions(c,self.state['id'])
        params=dict(id=self.state['id'],revision=0)
        viewed=self.call('training.review',params);self.assertEqual(set(viewed['quotes']),{'open'});self.assertIsNotNone(viewed['opening'])
        self.call('training.note',params|dict(note='事后补写：等待确认',mistake=True));viewed=self.call('training.review',params);self.assertTrue(viewed['mistake']);self.assertEqual(viewed['note'],'事后补写：等待确认')
        with self.service.store.transaction() as c:self.assertEqual(before,self.service.store.actions(c,self.state['id']))
        replay=self.call('training.replay',dict(id=self.state['id'],revision=2));self.assertEqual(replay['phase'],'CLOSE');self.assertIn('T+1',replay['reasons']['SELL']);self.assertTrue(replay['seen']);self.assertEqual(replay['kind'],'replay');self.assertEqual(replay['revision'],0);self.assertNotIn('reveal',replay)
        new=self.call('training.start',{'length':60});self.assertTrue(new['seen']);self.assertEqual(self.call('training.list',{})['items'][0]['kind'],'blind')
    def test_complete_benchmark_and_no_forced_sale(self):
        self.state=self.call('training.act',self.act_params('BUY'))
        for _ in range(120):self.state=self.call('training.act',self.act_params('HOLD'))
        s=self.state;self.assertEqual(s['status'],'completed');self.assertEqual(len(s['actions']),121);self.assertEqual(s['rounds_completed'],120);self.assertTrue(s['review']['eligible']);self.assertGreater(s['account']['units'],0)
        self.assertAlmostEqual(s['review']['return_percent'],5);self.assertAlmostEqual(s['review']['benchmark_return'],s['review']['return_percent']);self.assertEqual(s['review']['trades'],1);self.assertEqual(s['review']['position_days'],60)
        self.assertAlmostEqual(s['review']['drawdown_percent'],(1-9/10.5)*100)
    def test_unknown_restricted_and_paused_finish(self):
        with writable(self.data.paths['status']) as c:c.execute("DELETE FROM equity_status_daily WHERE trade_date='2024-01-03'")
        self.state=self.call('training.act',self.act_params('HOLD'));self.state=self.call('training.act',self.act_params('HOLD'))
        self.assertTrue(self.state['restricted']);self.assertIn('规则',self.state['reasons']['BUY']);old=self.state['revision']
        self.state=self.call('training.act',self.act_params('HOLD'));self.assertEqual(self.state['revision'],old+1)
        self.state=self.finish();self.assertFalse(self.state['review']['eligible'])
    def test_source_missing_can_end_preserve_last_nav(self):
        self.state=self.call('training.act',self.act_params('BUY'))
        with writable(self.data.paths['raw']) as c:c.execute("DELETE FROM equity_daily_raw WHERE trade_date='2024-01-03'")
        self.state=self.call('training.act',self.act_params('HOLD'));self.state=self.call('training.act',self.act_params('HOLD'));self.assertEqual(self.state['status'],'paused');nav=self.state['account']['nav']
        self.state=self.finish();self.assertEqual(self.state['status'],'revealed');self.assertEqual(self.state['account']['nav'],nav);self.assertTrue(self.state['review']['source_invalid'])
    def test_abandon_after_reveal_still_seen_and_reviewable(self):
        self.state=self.call('training.act',self.act_params('HOLD'));self.state=self.finish()
        self.state=self.call('training.abandon',dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase']))
        self.assertEqual(self.state['status'],'abandoned');self.assertIn('reveal',self.state)
        self.call('training.review',dict(id=self.state['id'],revision=0))
        new=self.call('training.start',{'length':60});self.assertTrue(new['seen'])
