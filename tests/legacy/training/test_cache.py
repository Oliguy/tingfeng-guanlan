import unittest,tempfile
from tests.legacy.training.test_service import fixture
from tests.legacy.training.test_projection import writable
from guanlan_data.repositories.training.data import readonly

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=fixture(self.temp.name)
        self.s={'code':'600001.SH','start':'2024-01-02','date':'2024-01-02','phase':'OPEN','settings':{'length':60}}
    def tearDown(self):self.data.close();self.temp.cleanup()
    def test_same_points_rules_and_prefixes(self):
        window=self.data.prepare(self.s)
        for phase in ('OPEN','CLOSE'):
            self.assertEqual(window.point(self.s['code'],self.s['date'],phase),self.data.point(self.s['code'],self.s['date'],phase))
        self.assertEqual(window.background(),self.data.background(self.s['code'],self.s['start']))
        self.assertEqual(len(window.history(self.s['code'],self.s['start'],self.s['date'],'OPEN')[1]),120)
        self.assertIs(window,self.data.prepare(self.s))
    def test_original_row_selects_not_repeated_and_tail_edit_not_adopted(self):
        window=self.data.prepare(self.s);before=window.fingerprints(self.s)
        # Fail on any new source row query; RO metadata polling remains permitted.
        from unittest.mock import patch
        with patch('guanlan_data.repositories.training.cache.readonly',side_effect=AssertionError('hot market SELECT')):
            for _ in range(4):
                self.assertEqual(self.data.prepare(self.s).fingerprints(self.s),before)
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_daily_raw SET close=10.8 WHERE trade_date='2024-01-02'")
        reloaded=self.data.prepare(self.s)
        self.assertIsNot(reloaded,window);self.assertEqual(reloaded.fingerprints(self.s),before)
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_adj_factor SET adj_factor=2 WHERE trade_date='2023-01-02'")
        self.assertNotEqual(self.data.prepare(self.s).fingerprints(self.s),before)
    def test_eviction_and_rebuild(self):
        windows=self.data.windows();windows.MAX_ENTRIES=1
        first=windows.prepare(self.s)
        windows.prepare(dict(self.s,settings={'length':150}))
        self.assertEqual(len(windows.entries),1)
        self.assertIsNot(windows.prepare(self.s),first)
        self.assertEqual(windows.prepare(self.s).fingerprints(self.s),first.fingerprints(self.s))
    def test_wal_data_version_is_seen(self):
        # This is a synthetic source, never the original market database.
        with writable(self.data.paths['raw']) as c:c.execute('PRAGMA journal_mode=WAL')
        before=self.data.prepare(self.s).fingerprints(self.s)
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_daily_raw SET open=10.1 WHERE trade_date='2024-01-02'")
        self.assertNotEqual(self.data.prepare(self.s).fingerprints(self.s)['point_hash'],before['point_hash'])

if __name__=='__main__':unittest.main()
