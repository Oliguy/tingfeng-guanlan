import copy,json,sqlite3,tempfile,unittest
from contextlib import contextmanager
from pathlib import Path
from datetime import date,timedelta
from unittest.mock import patch
from tests.legacy.industry_index.tests import fixture
from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_data.repositories.observer_collections.quotes import references; from guanlan_data.repositories.observer_collections.quotes import load; from guanlan_data.repositories.observer_collections.quotes import bars

BASE=Path(tempfile.gettempdir())

@contextmanager
def writable(path):
    c=sqlite3.connect(path)
    try:
        with c:yield c
    finally:c.close()

class QuoteReferences(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.db=Path(self.tmp.name)/'market.sqlite';self.support=self.db.with_name('support.sqlite')
        self.calendar_env=patch.dict('os.environ',{'OBSERVER_CALENDAR_ROOT':str(Path(self.tmp.name)/'calendar')})
        self.calendar_env.start();self.addCleanup(self.calendar_env.stop)
        _,self.m=fixture(240);self.code='000001.SZ';self.m['actual_end']=self.m['dates'][-1]
        with writable(self.db) as c:
            c.executescript('CREATE TABLE equity_master(ts_code TEXT PRIMARY KEY,list_date TEXT,list_status TEXT); CREATE TABLE equity_daily_raw(ts_code TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,vol_lot REAL,amount_thousand_cny REAL,PRIMARY KEY(ts_code,trade_date)); CREATE TABLE equity_adj_factor(ts_code TEXT,trade_date TEXT,adj_factor REAL,PRIMARY KEY(ts_code,trade_date)); CREATE TABLE trade_calendar(exchange TEXT,cal_date TEXT,is_open INTEGER);')
            for code,rows in self.m['quotes'].items():
                c.execute('INSERT INTO equity_master VALUES(?,?,?)',(code,'20200101','L'))
                for d,r in rows.items():
                    c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?,?)',(code,d,*[r[k] for k in ('open','high','low','close','vol_lot','amount_thousand_cny')]))
                    c.execute('INSERT INTO equity_adj_factor VALUES(?,?,?)',(code,d,r['adj_factor']))
            c.executemany('INSERT INTO trade_calendar VALUES(?,?,1)',[('SSE',d) for d in self.m['dates']])
            a,b=map(date.fromisoformat,(self.m['dates'][0],self.m['dates'][-1]));known=set(self.m['dates'])
            c.executemany('INSERT INTO trade_calendar VALUES(?,?,0)',[('SSE',(a+timedelta(days=i)).isoformat())
                for i in range((b-a).days+1) if (a+timedelta(days=i)).isoformat() not in known])
        self.ref=references(self.m,self.db,self.support)[self.code]['ref']
    def tearDown(self):self.tmp.cleanup()
    def test_reference_has_no_quotes_and_matches_bounded_values(self):
        self.assertNotIn('rows',self.ref);self.assertNotIn('open',json.dumps(self.ref))
        payload=load(self.ref);self.assertEqual(len(payload['rows']),261)
        daily,_,_=bars(self.ref,self.m['display_dates'][0],sessions=self.m['display_dates'])
        self.assertEqual(daily[-1]['close'],10);self.assertEqual(daily[-1]['volume'],100)
        weekly,_,_=bars(self.ref,self.m['display_dates'][0],'weekly');self.assertTrue(weekly[-1]['thirty_week_ma'])
    def test_historical_price_factor_and_calendar_changes_rejected(self):
        for table,field,value in [('equity_daily_raw','close',11),('equity_adj_factor','adj_factor',2),('trade_calendar','is_open',0)]:
            with self.subTest(table=table):
                with writable(self.db) as c:
                    where='cal_date' if table=='trade_calendar' else 'trade_date'
                    old=c.execute(f'SELECT {field} FROM {table} WHERE {where}=?',(self.m['dates'][-1],)).fetchone()[0]
                    c.execute(f'UPDATE {table} SET {field}=? WHERE {where}=?',(value,self.m['dates'][-1]))
                with self.assertRaisesRegex(ValueError,'行情已修订'):load(self.ref)
                with writable(self.db) as c:c.execute(f'UPDATE {table} SET {field}=? WHERE {where}=?',(old,self.m['dates'][-1]))
    def test_newer_rows_do_not_change_pinned_quote(self):
        with writable(self.db) as c:
            c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?,?)',(self.code,'2026-01-01',20,20,20,20,3,4))
            c.execute('INSERT INTO equity_adj_factor VALUES(?,?,?)',(self.code,'2026-01-01',2))
        self.assertEqual(load(self.ref)['rows'][-1]['close'],10)
    def test_factor_split_and_confirmed_halt_keep_units(self):
        days=self.m['display_dates'];halt=days[-2]
        with writable(self.db) as c:
            c.execute('DELETE FROM equity_daily_raw WHERE ts_code=? AND trade_date=?',(self.code,halt))
            c.execute('UPDATE equity_daily_raw SET open=5,high=5,low=5,close=5 WHERE ts_code=? AND trade_date=?',(self.code,days[-1]))
            c.execute('UPDATE equity_adj_factor SET adj_factor=2 WHERE ts_code=? AND trade_date=?',(self.code,days[-1]))
        del self.m['quotes'][self.code][halt];self.m['quotes'][self.code][days[-1]].update(open=5,high=5,low=5,close=5,adj_factor=2)
        self.m['suspensions']={self.code:{halt:'confirmed-test-evidence'}}
        ref=references(self.m,self.db,self.support)[self.code]['ref'];rows,_,events=bars(ref,days[0],sessions=days)
        self.assertEqual(rows[0]['close'],5);self.assertEqual(rows[-1]['volume'],100)
        self.assertEqual(rows[-2]['status'],'suspended');self.assertIsNone(rows[-2]['close']);self.assertEqual(len(events),1)

if __name__=='__main__':unittest.main()
