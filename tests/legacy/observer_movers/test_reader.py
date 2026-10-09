import copy,sqlite3,tempfile,unittest
from unittest.mock import patch
from contextlib import closing
from pathlib import Path
from guanlan_data.repositories.observer_movers.reader import Reader
from guanlan_data.repositories.observer_movers.limits import same_price

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=tempfile.gettempdir());self.addCleanup(self.tmp.cleanup)
        paths_env=patch.dict('os.environ',{'OBSERVER_COLLECTION_ROOT':str(Path(self.tmp.name)/'collections'),'OBSERVER_CALENDAR_ROOT':str(Path(self.tmp.name)/'calendar')})
        paths_env.start();self.addCleanup(paths_env.stop)
        self.market=Path(self.tmp.name)/'quotes.sqlite';self.support=Path(self.tmp.name)/'support.sqlite'
        self.catalog={'revision':'r1','groups':[{'id':'I1','name':'行业一','kind':'industry','members':[{'code':'000001.SZ','name':'一'},{'code':'000001.SZ','name':'一'}]}, {'id':'I2','name':'行业二','kind':'industry','members':[{'code':'000001.SZ','name':'一'}]}],
                      'themes':[{'id':'T1','name':'甲','revision':1,'members':[{'code':'000001.SZ','paths':[['AI','自研']]}],'tags':[{'id':'L1','theme_id':'T1','path':['AI'],'name':'AI','order':0}]}]}
        with closing(sqlite3.connect(self.market)) as c,c:
            c.executescript('CREATE TABLE equity_daily_raw(ts_code TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,pre_close REAL,pct_chg REAL,vol_lot REAL,amount_thousand_cny REAL,PRIMARY KEY(ts_code,trade_date));CREATE INDEX dates ON equity_daily_raw(trade_date,ts_code);CREATE TABLE equity_master(ts_code TEXT PRIMARY KEY,name TEXT,list_date TEXT);CREATE TABLE equity_adj_factor(ts_code TEXT,trade_date TEXT,adj_factor REAL,PRIMARY KEY(ts_code,trade_date));CREATE TABLE trade_calendar(exchange TEXT,cal_date TEXT,is_open INTEGER);')
            self.put(c,'000001.SZ',7.00001);self.put(c,'000002.SZ',7);self.put(c,'000003.SZ',-7);self.put(c,'000004.SZ',-7.00001);self.put(c,'000005.SZ',None,10.8)
            self.put(c,'000001.SZ',8,day='2026-09-28')
            c.execute('ALTER TABLE equity_master ADD COLUMN list_status TEXT')
            c.execute("INSERT INTO equity_master SELECT ts_code,ts_code,REPLACE(MIN(trade_date),'-',''),'L' FROM equity_daily_raw GROUP BY ts_code")
            c.executemany("INSERT INTO trade_calendar VALUES('SSE',?,1)",[(d,) for d in ('2026-09-28','2026-09-29','2026-09-30')])
        import guanlan_data.repositories.observer_series.sources as sources
        from guanlan_data.repositories.observer_series.build import build
        build(configured={**sources.paths(),'stock':self.market,'support':self.support},kinds=('stock',),job_id='movers-fixture')
        self.reader=Reader(self.market,support=self.support,catalog=lambda:copy.deepcopy(self.catalog))

    def put(self,c,code,pct,close=10.8,day='2026-09-30'):
        c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?,?,?,?)',(code,day,10,11,9,close,10,pct,100,1000))
        c.execute('INSERT INTO equity_adj_factor VALUES(?,?,1)',(code,day))

    def test_strict_boundary_fallback_and_unclassified(self):
        data=self.reader.query({'view':'day'})
        self.assertEqual({r['code'] for r in data['rows']},{'000001.SZ','000004.SZ','000005.SZ'})
        self.assertEqual(data['date'],'2026-09-30');self.assertEqual(data['previous_date'],'2026-09-28');self.assertIsNone(data['next_date'])
        self.assertEqual(data['stats']['total'],3)
        self.assertEqual(sum(len(g['codes']) for g in data['groups']['industry']),4)
        self.assertEqual(data['rows'][0]['themes'][0]['tags'][0]['theme_id'],'T1')
        self.assertTrue(all(r['limit']['status']=='unrestricted' for r in data['rows']))
        self.assertTrue(all(not r['limit_sequence']['label'] for r in data['rows']))
        self.assertFalse(self.support.exists())

    def test_history_neighbors_date_pagination(self):
        data=self.reader.query({'view':'day','date':'2026-09-28'})
        self.assertIsNone(data['previous_date']);self.assertEqual(data['next_date'],'2026-09-30')
        page=self.reader.query({'view':'dates','limit':1});self.assertEqual(page['next_cursor'],'2026-09-30')
        self.assertEqual(self.reader.query({'view':'dates','before':page['next_cursor']})['dates'],['2026-09-28'])

    def test_correction_invalidates_cache_and_stock_fence(self):
        old=self.reader.day();r=old['rows'][0]
        with closing(sqlite3.connect(self.market)) as c,c:c.execute("UPDATE equity_daily_raw SET pct_chg=9 WHERE ts_code='000001.SZ' AND trade_date='2026-09-30'")
        new=self.reader.day();self.assertNotEqual(old['data_revision'],new['data_revision'])
        with self.assertRaisesRegex(ValueError,'修订'):self.reader.stock({'date':old['date'],'target':{'kind':'stock','id':r['code']},'event_revision':r['event_revision']})

    def test_stock_has_no_classification_requirement(self):
        r=next(r for r in self.reader.day()['rows'] if r['code']=='000004.SZ')
        data=self.reader.stock({'date':'2026-09-30','target':{'kind':'stock','id':r['code']},'event_revision':r['event_revision']})
        self.assertEqual(data['parent']['kind'],'movers');self.assertEqual(data['chart_bars'][-1]['close'],10.8)
        self.assertEqual(data['stock']['quote']['change'],r['pct_chg']/100)

    def test_missing_factor_does_not_remove_anomaly(self):
        with closing(sqlite3.connect(self.market)) as c,c:c.execute("DELETE FROM equity_adj_factor WHERE ts_code='000004.SZ'")
        self.assertIn('000004.SZ',{r['code'] for r in self.reader.day()['rows']})
        with self.assertRaisesRegex(ValueError,'复权'):self.reader.stock({'date':'2026-09-30','target':{'kind':'stock','id':'000004.SZ'}})
        self.assertEqual(len(self.reader.stock({'date':'2026-09-30','target':{'kind':'stock','id':'000004.SZ'},'price_mode':'raw'})['chart_bars']),1)

    def test_classification_fence_and_missing_change_invalid_price(self):
        old=self.reader.day();self.catalog['revision']='r2'
        with self.assertRaisesRegex(ValueError,'分类目录'):self.reader.stock({'date':old['date'],'target':{'kind':'stock','id':'000001.SZ'},'classification_revision':old['classification_revision']})
        with closing(sqlite3.connect(self.market)) as c,c:
            c.execute("UPDATE equity_daily_raw SET pre_close=0,pct_chg=NULL WHERE ts_code='000005.SZ'")
            c.execute("UPDATE equity_daily_raw SET high=8 WHERE ts_code='000004.SZ'")
        current=self.reader.day();self.assertEqual(current['quality']['unavailable_change'],1)
        self.assertEqual(current['quality']['invalid_ohlc'],1)
        self.assertIn('000004.SZ',{r['code'] for r in current['rows']})
        with self.assertRaisesRegex(ValueError,'OHLC'):self.reader.stock({'date':old['date'],'target':{'kind':'stock','id':'000004.SZ'}})

    def test_invalid_inputs_and_tick(self):
        self.assertTrue(same_price(10.999999,11));self.assertFalse(same_price(float('nan'),11))
        for params in ({'view':'dates','limit':10000},{'view':'day','date':'20260930'},{'view':'day','database':'secret'}):
            with self.assertRaises(ValueError):self.reader.query(params)

    def test_limit_source_failure_does_not_hide_list(self):
        self.support.write_bytes(b'not a sqlite database')
        data=self.reader.day();self.assertEqual(data['stats']['total'],3)
        self.assertEqual(data['quality']['limit_coverage']['status'],'unavailable')
        self.assertEqual(data['stats']['limit_unknown'],3)

    def test_five_session_union_keeps_older_stock_and_missing_dates(self):
        with closing(sqlite3.connect(self.market)) as c,c:
            c.executemany("INSERT INTO trade_calendar VALUES('SSE',?,1)",[(d,) for d in ('2026-09-24','2026-09-25')])
            self.put(c,'000009.SZ',8,day='2026-09-28')
            c.execute("INSERT INTO equity_master VALUES('000009.SZ','历史异动','20200101','L')")
        result=self.reader.query({'view':'leader','date':'2026-09-30'})
        window=result['window']
        self.assertEqual(window['dates'],['2026-09-24','2026-09-25','2026-09-28','2026-09-29','2026-09-30'])
        self.assertEqual(window['missing_dates'],['2026-09-24','2026-09-25','2026-09-29'])
        self.assertFalse(window['complete'])
        row=next(r for r in window['rows'] if r['code']=='000009.SZ')
        self.assertEqual(row['event_date'],'2026-09-28')
        self.assertIn(row['code'],result['scores'])
        self.assertIsNone(result['scores'][row['code']]['score'])

    def test_older_event_chart_extends_to_selected_day(self):
        with closing(sqlite3.connect(self.market)) as c,c:
            c.execute("UPDATE equity_daily_raw SET pre_close=10.8,pct_chg=0 WHERE ts_code='000001.SZ' AND trade_date='2026-09-30'")
        old=next(r for r in self.reader.day('2026-09-28')['rows'] if r['code']=='000001.SZ')
        result=self.reader.query({'view':'stock','date':'2026-09-30','event_date':'2026-09-28',
                                  'target':{'kind':'stock','id':'000001.SZ'},'event_revision':old['event_revision'],'price_mode':'raw'})
        self.assertEqual(result['event']['date'],'2026-09-28')
        self.assertEqual(result['price_date'],'2026-09-30')
        self.assertEqual(result['stock']['quote']['change'],0)
        with self.assertRaisesRegex(ValueError,'晚于'):
            self.reader.query({'view':'stock','date':'2026-09-28','event_date':'2026-09-30','target':{'kind':'stock','id':'000001.SZ'}})

if __name__=='__main__':unittest.main()
