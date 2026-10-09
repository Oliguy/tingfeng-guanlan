"""User-observable regressions from the 2026-10-07 start/resume audit."""
import unittest,tempfile,uuid
from tests.legacy.training.test_service import fixture; from tests.legacy.training.test_service import writable
from guanlan_data.repositories.training.data import Data
from guanlan_app.features.training.service import Service

class StartResumeAuditTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=tempfile.gettempdir(),prefix='training-audit-test-')
        self.data=fixture(self.folder.name);self.service=Service(self.data)
    def tearDown(self):self.service.close();self.folder.cleanup()
    def call(self,op,params,request=None):return self.service.invoke(op,params,request or str(uuid.uuid4()))
    def test_start_rejects_corrupt_visible_background(self):
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_daily_raw SET vol_lot=NULL WHERE trade_date='2023-01-02'")
        with self.assertRaisesRegex(ValueError,'没有可核实'):
            Data(self.data.paths).choose(1,{'market':'all','from':'2024-01-02','to':'2024-01-02'})
    def test_phase_action_and_retry_preserve_selected_chart_period(self):
        s=self.call('training.start',{'length':60,'period':'weekly'})
        self.assertEqual(s['period'],'weekly')
        params=dict(id=s['id'],expected_revision=0,expected_phase='OPEN',action='HOLD',period='monthly')
        request=str(uuid.uuid4());s=self.call('training.act',params,request)
        self.assertEqual((s['period'],s['phase'],s['revision']),('monthly','CLOSE',1))
        repeat=self.call('training.act',params,request)
        self.assertEqual(repeat,s)
    def test_invalid_chart_period_does_not_pause_or_write_session(self):
        s=self.call('training.start',{'length':60})
        with self.assertRaisesRegex(ValueError,'周期无效'):self.call('training.state',{'id':s['id'],'period':'bad'})
        with self.service.store.transaction() as c:saved=self.service.store.get(c,s['id'])
        self.assertEqual((saved['status'],saved['revision']),('active',0))

if __name__=='__main__':unittest.main()
