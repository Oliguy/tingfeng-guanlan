"""Regressions for the user's buy/Space/terminal-state report."""
import tempfile
import unittest
import uuid
from guanlan_data.repositories.training.data import Data
from guanlan_app.features.training.service import Service
from tests.legacy.training.test_service import fixture; from tests.legacy.training.test_service import writable

class TradeDisplayTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=tempfile.gettempdir(), prefix='training-trade-test-')
        self.data = fixture(self.folder.name)
        self.service = Service(self.data)
    def tearDown(self):
        self.service.close()
        self.folder.cleanup()
    def call(self, op, params):
        return self.service.invoke(op, params, str(uuid.uuid4()))
    def act(self, s, action):
        return self.call('training.act', dict(id=s['id'], expected_revision=s['revision'], expected_phase=s['phase'], action=action))
    def test_new_blind_start_rejects_calendar_terminal_day(self):
        last = self.data.dates('600001.SH')[-1]
        with self.assertRaisesRegex(ValueError, '没有可核实'):
            Data(self.data.paths).choose(1, {'from': last, 'to': last})
    def test_stock_short_tail_rejected_for_fixed_length(self):
        # v0.3 explicitly requires the requested complete stock window.
        with writable(self.data.paths['raw']) as c:
            c.execute("DELETE FROM equity_daily_raw WHERE trade_date>'2024-01-02'")
            c.execute("DELETE FROM equity_adj_factor WHERE trade_date>'2024-01-02'")
        with self.assertRaisesRegex(ValueError,'完整150'):
            Data(self.data.paths).choose(1, {'from': '2024-01-02', 'to': '2024-01-02'})
    def test_review_position_matches_operation_after_cost_and_sale(self):
        s = self.call('training.start', {'length': 60})
        s = self.act(s, 'BUY')
        cost = s['account']['cost']
        s = self.act(s, 'HOLD')
        s = self.act(s, 'HOLD')
        s = self.act(s, 'SELL')
        s = self.call('training.finish', dict(id=s['id'], expected_revision=s['revision'], expected_phase=s['phase']))
        self.assertEqual(s['account']['cost'], 0)
        buy = self.call('training.review', dict(id=s['id'], revision=0))
        hold = self.call('training.review', dict(id=s['id'], revision=1))
        sell = self.call('training.review', dict(id=s['id'], revision=3))
        self.assertAlmostEqual(buy['position']['cost'], cost)
        self.assertEqual(hold['position'], buy['position'])
        self.assertEqual(sell['position'], {'units': 0, 'cost': 0})

if __name__ == '__main__':
    unittest.main()
