import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from tests.legacy.observer_collections.test_quotes import BASE; from tests.legacy.observer_collections.test_quotes import writable
from guanlan_data.repositories.observer_collections.quotes import daily_volume_ratio
from guanlan_data.repositories.observer_collections.member_products import project
from guanlan_data.repositories.stock_profile.products import summaries


class DailyVolumeRatio(unittest.TestCase):
    def setUp(self):
        self.days = ['2026-09-23', '2026-09-24', '2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30']
        self.rows = [{'trade_date': d, 'vol_lot': v} for d, v in zip(self.days, [10, 20, 30, 40, 50, 60])]

    def test_five_previous_open_sessions_exclude_today(self):
        result = daily_volume_ratio(self.rows, self.days)
        self.assertEqual(result['volume_ratio'], 2)
        self.assertEqual(result['volume_ratio_date'], '2026-09-30')

    def test_missing_quote_is_not_skipped_or_treated_as_suspension(self):
        self.assertIsNone(daily_volume_ratio(self.rows[1:], self.days)['volume_ratio'])
        self.assertIsNone(daily_volume_ratio(self.rows[:-1], self.days)['volume_ratio'])
        self.assertIsNone(daily_volume_ratio(self.rows[-5:], self.days[-5:])['volume_ratio'])

    def test_confirmed_suspensions_use_zero_volume(self):
        self.assertEqual(daily_volume_ratio(self.rows[1:], self.days, {self.days[0]: 'confirmed'})['volume_ratio'], 60 / 28)
        self.assertEqual(daily_volume_ratio(self.rows[:-1], self.days, {self.days[-1]: 'confirmed'})['volume_ratio'], 0)

    def test_zero_denominator_and_invalid_volumes_are_unknown(self):
        rows = [{'trade_date': d, 'vol_lot': 0} for d in self.days]
        self.assertIsNone(daily_volume_ratio(rows, self.days)['volume_ratio'])
        for invalid in (None, -1, float('nan'), float('inf'), True):
            with self.subTest(invalid=invalid):
                rows = [dict(r) for r in self.rows];rows[0]['vol_lot'] = invalid
                self.assertIsNone(daily_volume_ratio(rows, self.days)['volume_ratio'])


class MemberProducts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'profiles.sqlite'
        with writable(self.db) as c:
            c.executescript('''CREATE TABLE sp_securities(code TEXT PRIMARY KEY,company_id INTEGER);
                CREATE TABLE sp_heads(company_id INTEGER,revision_id INTEGER,period_end TEXT);
                CREATE TABLE sp_revision_documents(revision_id INTEGER,document_id INTEGER);
                CREATE TABLE sp_documents(id INTEGER PRIMARY KEY,company_id INTEGER,period_end TEXT,kind TEXT);
                CREATE TABLE sp_facts(id INTEGER PRIMARY KEY,document_id INTEGER,original_name TEXT,products_json TEXT,stage TEXT,status TEXT);
                INSERT INTO sp_securities VALUES('000001.SZ',1),('600002.SH',2);
                INSERT INTO sp_heads VALUES(1,5,'2026-06-30'),(2,6,'2026-06-30');
                INSERT INTO sp_documents VALUES(11,1,'2026-06-30','half_year'),(12,1,'2025-12-31','annual'),(13,1,'2026-06-30','half_year'),(21,2,'2026-06-30','half_year');
                INSERT INTO sp_revision_documents VALUES(5,11),(5,12),(6,21);''')
            c.executemany('INSERT INTO sp_facts VALUES(?,?,?,?,?,?)', [
                (1, 11, '业务A', json.dumps(['甲产品', '乙产品']), 'actual', 'source_supported'),
                (2, 11, '研发业务', json.dumps(['研发产品']), 'development', 'model_initial'),
                (3, 12, '旧期间', json.dumps(['旧期间产品']), 'actual', 'verified'),
                (4, 13, '归档版本', json.dumps(['归档产品']), 'actual', 'verified'),
                (5, 21, '其他公司', json.dumps(['其他公司产品']), 'actual', 'verified')])

    def test_current_profiles_use_active_docs_and_period_and_preserve_status(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        item = summaries(self.db, ['000001.SZ'])['000001.SZ']
        self.assertEqual(item['products'], ['甲产品', '乙产品'])
        self.assertEqual(item['statuses'], ['source_supported'])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_referenced_facts_stay_pinned_and_never_fall_back_to_another_company(self):
        item = summaries(self.db, ['000001.SZ'], fact_ids={'000001.SZ': [4]})['000001.SZ']
        self.assertEqual(item['products'], ['归档产品'])
        item = summaries(self.db, ['000001.SZ'], fact_ids={'000001.SZ': [5, 999]})['000001.SZ']
        self.assertEqual(item['products'], [])

    def test_working_subindustry_products_are_reused_without_promoting_status(self):
        member = {'code': '000001.SZ', 'status': 'unverified', 'relations': [
            {'product': '子板块产品', 'stage': 'actual'}, {'product': '未投产产品', 'stage': 'development'}]}
        item = project([member], self.db)['000001.SZ']
        self.assertEqual(item['products'], ['子板块产品'])
        self.assertIn('未核实', item['product_note'])
        self.assertEqual(member['status'], 'unverified')

    def test_duplicate_products_merge_and_missing_profile_does_not_create_a_database(self):
        member = {'code': '000001.SZ', 'relations': [{'fact_id': 1}, {'fact_id': 1}]}
        self.assertEqual(project([member], self.db)['000001.SZ']['products'], ['甲产品', '乙产品'])
        missing = self.db.with_name('missing.sqlite')
        self.assertEqual(summaries(missing, ['000001.SZ'])['000001.SZ']['products'], [])
        self.assertFalse(missing.exists())

    def test_product_disclosures_fill_profile_gaps_without_using_industry_or_region_labels(self):
        with writable(self.db) as c:
            c.executescript('''CREATE TABLE sp_contexts(id INTEGER,document_id INTEGER,label TEXT,dimension TEXT,scope TEXT,period_end TEXT,status TEXT);
                INSERT INTO sp_securities VALUES('300003.SZ',3);
                INSERT INTO sp_heads VALUES(3,7,'2026-06-30');
                INSERT INTO sp_documents VALUES(31,3,'2026-06-30','structured_financial');
                INSERT INTO sp_revision_documents VALUES(7,31);
                INSERT INTO sp_contexts VALUES(1,31,'重组蛋白','product','consolidated','2026-06-30','source_supported'),
                (2,31,'试剂','product','consolidated','2026-06-30','source_supported'),
                (3,31,'试剂','product','consolidated','2026-06-30','source_supported'),
                (4,31,'国内','region','consolidated','2026-06-30','source_supported'),
                (5,31,'医药','industry','consolidated','2026-06-30','source_supported'),
                (6,31,'旧产品','product','consolidated','2025-12-31','source_supported');''')
        result = summaries(self.db, ['300003.SZ'])['300003.SZ']
        self.assertEqual(result['products'], ['重组蛋白', '试剂'])
        self.assertEqual(result['basis'], 'current_product_disclosure')
        pinned = summaries(self.db, ['300003.SZ'], fact_ids={'300003.SZ': [999]})['300003.SZ']
        self.assertEqual(pinned['products'], [])
