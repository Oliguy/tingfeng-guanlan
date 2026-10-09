import json,tempfile,unittest
from pathlib import Path
import guanlan_data.repositories.observer_collections.catalog as catalog
from tests.legacy.observer_collections.test_quotes import QuoteReferences; from tests.legacy.observer_collections.test_quotes import BASE

class ThemeCatalog(QuoteReferences):
    def setUp(self):
        super().setUp();self.path=Path(self.tmp.name)/'catalog.sqlite'
        from tests.legacy.observer_collections.test_quotes import writable
        with writable(self.db) as c:
            c.execute('ALTER TABLE equity_master ADD COLUMN name TEXT')
            c.execute("UPDATE equity_master SET name=CASE ts_code WHEN '000001.SZ' THEN '股票甲' ELSE '股票乙' END")
        self.payload={'name':'题材甲','members':[{'code':'000001.SZ','paths':[['上游','芯片']]},{'code':'000001.SZ','paths':[['自研']]},{'code':'股票乙','paths':[]}],'source':{'title':'用户资料'}}
    def preview(self,**kw):return catalog.preview(kw.pop('payload',self.payload),path=self.path,market_path=self.db,**kw)
    def commit(self,d,rid='request'):return catalog.commit(d,rid,self.path,self.db)
    def test_read_does_not_initialize_database(self):
        self.assertEqual(catalog.list_themes(self.path),[]);self.assertFalse(self.path.exists())
    def test_merge_duplicate_stock_but_keep_paths_and_idempotency(self):
        d=self.preview();self.assertEqual(d['member_count'],2);self.assertEqual(d['tag_relations'],2);self.assertEqual(d['duplicate_rows_merged'],1)
        first=self.commit(d);self.assertEqual(first,self.commit(d))
        saved=catalog.get(first['id'],self.path);self.assertEqual(len(saved['members'][0]['paths']),2)
        self.assertEqual(len(catalog.list_themes(self.path)),1)
        with self.assertRaises(ValueError):self.commit(self.preview(),'request')
    def test_same_label_scoped_to_theme_and_foreign_tag_ids_rejected(self):
        a=self.preview();b=self.preview();self.commit(a,'a');self.commit(b,'b')
        self.assertNotEqual(a['payload']['tags'][0]['id'],b['payload']['tags'][0]['id'])
        with self.assertRaises(ValueError):self.preview(payload={'name':'bad','members':[{'code':'000001.SZ','tag_ids':[a['payload']['tags'][0]['id']]}]})
    def test_revision_conflict_and_historical_members_unchanged(self):
        first=self.commit(self.preview());tid=first['id']
        edit={'name':'新名字','members':[{'code':'000001.SZ','paths':[['新标签']]}]}
        a=self.preview(payload=edit,theme_id=tid,expected_revision=1);b=self.preview(theme_id=tid,expected_revision=1)
        self.commit(a,'new')
        with self.assertRaisesRegex(ValueError,'版本冲突'):self.commit(b,'stale')
        self.assertEqual(catalog.get(tid,self.path,1)['name'],'题材甲');self.assertEqual(catalog.get(tid,self.path)['name'],'新名字')
    def test_archive_restore_preserve_members_and_never_change_prices(self):
        before=self.db.read_bytes();first=self.commit(self.preview());tid=first['id']
        a=catalog.archive(tid,1,'a',path=self.path);self.assertEqual(a,catalog.archive(tid,1,'a',path=self.path))
        self.assertEqual(catalog.list_themes(self.path),[]);self.assertEqual(len(catalog.list_themes(self.path,True)),1)
        catalog.archive(tid,2,'r',restore=True,path=self.path)
        self.assertEqual(catalog.get(tid,self.path)['revision'],3);self.assertEqual(len(catalog.get(tid,self.path)['members']),2)
        self.assertEqual(before,self.db.read_bytes())
    def test_unknown_identity_not_silently_omitted_from_commit(self):
        d=self.preview(payload={'name':'缺身份','members':[{'code':'不存在','paths':[]}]})
        self.assertTrue(d['issues'])
        with self.assertRaises(ValueError):self.commit(d)
        self.assertFalse(self.path.exists())
    def test_no_ohlc_allowed_in_metadata(self):
        with self.assertRaises(ValueError):self.preview(payload={**self.payload,'prices':[]})
        with self.assertRaises(ValueError):self.preview(payload={'name':'x','members':[{'code':'000001.SZ','close':10}]})


if __name__=='__main__':unittest.main()
