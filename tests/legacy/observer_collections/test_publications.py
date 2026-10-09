"""Atomic publication and cross-collection isolation using small deterministic fixtures."""
import copy,json
from unittest.mock import patch
import guanlan_data.repositories.observer_collections.catalog as catalog; import guanlan_data.repositories.observer_collections.store as store; import guanlan_data.repositories.observer_collections.reader as reader
from guanlan_data.repositories.observer_collections.quotes import references
from guanlan_app.features.observer_collections.build import calculate_publish
from tests.legacy.observer_collections.test_quotes import QuoteReferences; from tests.legacy.observer_collections.test_quotes import writable

class Publications(QuoteReferences):
    def setUp(self):
        super().setUp();self.output=self.db.with_name('results.sqlite');self.catalog=self.db.with_name('catalog.sqlite')
        # Current readers share the persisted-series schema in the derived DB.
        # Initialize that isolated fixture without generating signals or prices.
        with store.connection(self.output):pass
        from guanlan_data.repositories.observer_series.store import initialize as initialize_series
        initialize_series(self.output)
        inputs=patch('guanlan_data.repositories.observer_collections.config.input_paths',return_value=(self.db,self.db));inputs.start();self.addCleanup(inputs.stop)
        with writable(self.db) as c:
            c.execute('ALTER TABLE equity_master ADD COLUMN name TEXT');c.execute("UPDATE equity_master SET name=ts_code")
        self.header={'actual_end':self.m['actual_end'],'start_date':self.m['display_dates'][0],'display_dates':self.m['display_dates']}
        self.refs=references(self.m,self.db,self.support)
        self.definition={'id':'I01','name':'sample','kind':'industry','parent_id':None,'revision':'r1','quality':'classified_sample',
                         'members':[{'code':self.code,'name':'A','status':'verified','relations':[]}]}
    def calculate(self,groups=None):return calculate_publish(groups or [self.definition],self.m,self.refs,self.output,self.catalog,self.header)
    def test_atomic_rollback_retains_current_and_no_price_payload(self):
        first=self.calculate();old=store.detail(self.output,'I01');new={**self.definition,'revision':'r2'}
        def crash():raise RuntimeError('crash')
        with self.assertRaisesRegex(RuntimeError,'crash'):
            store.publish(self.output,new,old['group'],self.header,self.refs,before_commit=crash)
        self.assertEqual(store.detail(self.output,'I01')['publication_id'],old['publication_id'])
        self.assertTrue(self.calculate()['published'][0]['idempotent'])
        with writable(self.output) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM quote_refs').fetchone()[0],1)
            for r in c.execute('SELECT ref_json FROM quote_refs'):self.assertNotIn('open',r[0]);self.assertNotIn('rows',r[0])
    def test_duplicate_member_does_not_double_weight_and_references_shared(self):
        other={**self.definition,'id':'N01','kind':'subindustry','parent_id':'I01','members':self.definition['members']*2}
        self.calculate([self.definition,other]);a=store.detail(self.output,'I01');b=store.detail(self.output,'N01')
        self.assertEqual(a['group']['points'][-1]['close'],b['group']['points'][-1]['close']);self.assertEqual(len(b['group']['members']),1)
        with writable(self.output) as c:self.assertEqual(c.execute('select count(*) from quote_refs').fetchone()[0],1)
    def test_empty_and_partial_failure_preserve_good_collections(self):
        self.calculate();old=store.detail(self.output,'I01')['publication_id']
        broken={**self.definition,'id':'bad','members':[{'code':'missing'}]};empty={**self.definition,'id':'empty','members':[]}
        result=self.calculate([broken,empty]);self.assertEqual(result['status'],'partial');self.assertEqual(len(result['failed']),1)
        self.assertEqual(store.detail(self.output,'empty')['group']['points'],[]);self.assertEqual(store.detail(self.output,'I01')['publication_id'],old)
    def make_theme(self):
        draft=catalog.preview({'name':'Theme','members':[{'code':self.code,'paths':[['A','B'],['C']]}]},path=self.catalog,market_path=self.db)
        result=catalog.commit(draft,'first',path=self.catalog,market_path=self.db);theme=catalog.get(result['id'],self.catalog)
        return {**theme,'kind':'theme','parent_id':None,'quality':'user_supplied'}
    def test_changed_revision_or_archive_prevents_stale_activation(self):
        theme=self.make_theme();self.calculate([theme]);old=store.detail(self.output,theme['id'])
        catalog.archive(theme['id'],1,'archive',path=self.catalog)
        with self.assertRaisesRegex(ValueError,'目录已变化'):store.publish(self.output,theme,old['group'],self.header,self.refs,catalog_path=self.catalog)
        self.assertEqual(store.detail(self.output,theme['id'])['publication_id'],old['publication_id'])
    def test_reader_membership_pin_and_revision_drift(self):
        theme=self.make_theme();self.calculate([theme]);id=theme['id'];pub=store.detail(self.output,id)['publication_id']
        with patch('guanlan_data.repositories.observer_collections.config.results_path',return_value=self.output),patch('guanlan_data.repositories.observer_collections.catalog.catalog_path',return_value=self.catalog):
            s=reader.query('theme',{'view':'summary'});self.assertEqual(len(s['items']),1)
            params={'view':'detail','target':{'kind':'stock','id':self.code},'parent_id':id,'publication_id':pub}
            self.assertTrue(reader.query('theme',params)['chart_bars'])
            with self.assertRaisesRegex(ValueError,'不属于'):reader.query('theme',{**params,'target':{'kind':'stock','id':'wrong'}})
            with writable(self.db) as c:c.execute('UPDATE equity_daily_raw SET close=11 WHERE ts_code=?',(self.code,))
            with self.assertRaisesRegex(ValueError,'行情已修订'):reader.query('theme',params)
            self.assertTrue(reader.query('theme',{'view':'detail','target':{'kind':'group','id':id},'publication_id':pub})['chart_bars'])
    def test_member_signal_source_token_matches_group_detail_and_invalidates(self):
        self.calculate();pub=store.detail(self.output,'I01')['publication_id']
        params={'target':{'kind':'group','id':'I01'},'publication_id':pub}
        with patch('guanlan_data.repositories.observer_collections.config.results_path',return_value=self.output):
            group=reader.query('industry30',{**params,'view':'detail'})
            signals=reader.query('industry30',{**params,'view':'member_signals'})
            self.assertEqual(group['member_signal_revision'],signals['source_revision'])
            with writable(self.db) as c:
                c.execute('UPDATE equity_adj_factor SET adj_factor=2 WHERE ts_code=? AND trade_date=?',(self.code,self.ref['end']))
            revised=reader.query('industry30',{**params,'view':'detail'})
            self.assertNotEqual(revised['member_signal_revision'],group['member_signal_revision'])
            self.assertEqual(reader.query('industry30',{**params,'view':'member_signals'})['items'][self.code]['status'],'unavailable')
    def test_pending_and_archived_theme_projection(self):
        theme=self.make_theme()
        with patch('guanlan_data.repositories.observer_collections.config.results_path',return_value=self.output),patch('guanlan_data.repositories.observer_collections.catalog.catalog_path',return_value=self.catalog):
            s=reader.query('theme',{'view':'summary'});self.assertTrue(s['items'][0]['pending']);self.assertIsNone(s['items'][0]['publication_id'])
            self.calculate([theme]);catalog.archive(theme['id'],1,'archive',path=self.catalog)
            self.assertFalse(reader.query('theme',{'view':'summary'})['items'])
            with self.assertRaisesRegex(ValueError,'已归档'):reader.query('theme',{'view':'detail','target':{'kind':'group','id':theme['id']}})

    def test_cached_group_copies_and_failure_change_do_not_hide_evidence(self):
        self.definition['members'][0]['relations']=[{'reason':'original evidence','stage':'actual','product':'test product'}]
        self.calculate()
        params={'view':'detail','target':{'kind':'group','id':'I01'}}
        with patch('guanlan_data.repositories.observer_collections.config.results_path',return_value=self.output):
            first=reader.query('industry30',params)
            # Pinned reads and initial current-publication prefetch share a
            # projection, while resolving the pointer again on every open.
            with patch.object(store,'detail',side_effect=AssertionError('decoded the same publication twice')):
                pinned=reader.query('industry30',{**params,'publication_id':first['data_revision']})
                self.assertEqual(pinned['data_revision'],first['data_revision'])
            with patch.object(store,'member_detail',side_effect=AssertionError('decoded cached members again')):
                signals=reader.query('industry30',{**params,'view':'member_signals','publication_id':first['data_revision']})
                self.assertEqual(signals['source_revision'],first['member_signal_revision'])
                signals['items'][self.code]['status']='modified by caller'
                clean=reader.query('industry30',{**params,'view':'member_signals','publication_id':first['data_revision']})
                self.assertNotEqual(clean['items'][self.code]['status'],'modified by caller')
            first['industry']['members'][0]['name']='modified by caller'
            second=reader.query('industry30',params)
            self.assertEqual(second['industry']['members'][0]['name'],'A')
            self.assertNotIn('relations',second['industry']['members'][0])
            self.assertEqual(store.detail(self.output,'I01')['group']['members'][0]['relations'][0]['reason'],'original evidence')
            store.failure(self.output,self.definition,'new publication failed')
            third=reader.query('industry30',params)
            self.assertEqual(third['failure'],'new publication failed')
