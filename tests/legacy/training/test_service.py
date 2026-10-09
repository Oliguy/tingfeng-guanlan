import unittest,tempfile,sqlite3,uuid,threading,json
from pathlib import Path
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
from guanlan_data.repositories.training.data import Data
from guanlan_data.repositories.training.store import Store
from guanlan_app.features.training.service import Service
from tests.legacy.training.test_projection import writable

class FixtureData(Data):
    def choose(self,seed,options):return {'code':'600001.SH','start':'2024-01-02'}

def fixture(root,kind='normal'):
    paths={key:Path(root)/(key+'.sqlite') for key in ('raw','status','support','store')}
    def trading(first,count):
        days=[];d=date.fromisoformat(first)
        while len(days)<count:
            if d.weekday()<5:days.append(d.isoformat())
            d+=timedelta(days=1)
        return days
    background=trading('2023-01-02',120);days=trading('2024-01-02',160)
    with writable(paths['raw']) as c:
        c.executescript('''CREATE TABLE equity_daily_raw(ts_code TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,pre_close REAL,vol_lot REAL,PRIMARY KEY(ts_code,trade_date));
         CREATE TABLE equity_adj_factor(ts_code TEXT,trade_date TEXT,adj_factor REAL,PRIMARY KEY(ts_code,trade_date));
         CREATE TABLE equity_master(ts_code TEXT PRIMARY KEY,name TEXT,market TEXT,list_date TEXT,delist_date TEXT,list_status TEXT);
         CREATE TABLE trade_calendar(exchange TEXT,cal_date TEXT,is_open INTEGER,PRIMARY KEY(exchange,cal_date));''')
        c.execute('INSERT INTO equity_master VALUES(?,?,?,?,?,?)',('600001.SH','合成测试','主板','2000-01-01',None,'L'))
        for day in background+days:
            o=11 if kind=='up' and day==days[0] else 10
            close=9 if kind in ('down','normal') and day==days[1] else 10.5
            c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?,?)',('600001.SH',day,o,11,9,close,10,100));c.execute('INSERT INTO equity_adj_factor VALUES(?,?,?)',('600001.SH',day,1));c.execute('INSERT INTO trade_calendar VALUES(?,?,?)',('SSE',day,1))
    with writable(paths['status']) as c:
        c.executescript('CREATE TABLE equity_status_daily(exchange TEXT,stock_code TEXT,trade_date TEXT,is_st INTEGER,is_delisted INTEGER,PRIMARY KEY(exchange,stock_code,trade_date)); CREATE TABLE namechange_source(ts_code,name,start_date,end_date,change_reason); CREATE TABLE metadata(key,value);')
        c.executemany('INSERT INTO equity_status_daily VALUES(?,?,?,?,?)',[('SH','600001',d,0,0) for d in background+days]);c.execute('INSERT INTO namechange_source VALUES(?,?,?,?,?)',('600001.SH','合成测试','2000-01-01',None,'正常上市'))
        c.executemany('INSERT INTO metadata VALUES(?,?)',[('coverage_start','2023-01-01'),('coverage_end',days[-1])])
    with writable(paths['support']) as c:c.execute('CREATE TABLE movers_limit_prices(trade_date TEXT,ts_code TEXT,up_limit REAL,down_limit REAL,status TEXT,PRIMARY KEY(trade_date,ts_code))')
    return FixtureData(paths)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.data=fixture(self.folder.name);self.service=Service(self.data);self.state=self.call('training.start',{'length':60})
    def tearDown(self):self.service.close();self.folder.cleanup()
    def call(self,op,params,id=None):return self.service.invoke(op,params,id or str(uuid.uuid4()))
    def act_params(self,action):return dict(id=self.state['id'],expected_revision=self.state['revision'],expected_phase=self.state['phase'],action=action)
    def test_atomic_idempotence_and_t1(self):
        params=self.act_params('BUY');request=str(uuid.uuid4());a=self.call('training.act',params,request);b=self.call('training.act',params,request)
        self.assertEqual(a,b);self.assertEqual(a['phase'],'OPEN');self.assertIn('T+1',a['reasons']['SELL']);self.assertEqual(a['account']['unlocked'],0)
        with self.assertRaisesRegex(ValueError,'页面状态'):self.call('training.act',params)
        self.state=a;self.state=self.call('training.act',self.act_params('HOLD'));self.assertEqual(self.state['phase'],'CLOSE');self.assertIn('T+1',self.state['reasons']['SELL'])
        self.state=self.call('training.act',self.act_params('HOLD'));self.assertEqual(self.state['day'],2);self.assertGreater(self.state['account']['unlocked'],0)
        self.state=self.call('training.act',self.act_params('HOLD'));self.assertIn('跌停',self.state['reasons']['SELL']);self.assertEqual(self.state['account']['currently_sellable'],0)
    def test_two_windows_and_simultaneous_phase(self):
        params=self.act_params('HOLD')
        def act():
            try:return self.call('training.act',params)['revision']
            except ValueError:return 'rejected'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:act(),range(2)))
        self.assertEqual(sorted(str(x) for x in results),['1','rejected'])
    def test_resume_and_no_public_identity(self):
        self.state=self.call('training.act',self.act_params('BUY'));reopened=Service(self.data)
        state=reopened.invoke('training.state',{'id':self.state['id']},str(uuid.uuid4()))
        self.assertEqual(self.state,state);self.assertNotIn('600001',json.dumps(state));self.assertNotIn('2024-01-02',json.dumps(state));self.assertNotIn('合成测试',json.dumps(state))
    def test_future_edits_allowed_adopted_factor_pauses(self):
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_daily_raw SET high=20,close=15 WHERE trade_date='2024-01-02'")
        state=self.call('training.state',{'id':self.state['id']});self.assertEqual(state['status'],'active');self.assertEqual(state['quotes'],self.state['quotes'])
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_adj_factor SET adj_factor=2 WHERE trade_date='2023-01-02'")
        state=self.call('training.state',{'id':self.state['id']});self.assertEqual(state['status'],'paused');self.assertEqual(state['revision'],0)
    def test_unrelated_append_and_save_failure(self):
        with writable(self.data.paths['raw']) as c:c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?,?)',('OTHER','2026-01-01',1,1,1,1,1,1))
        self.assertEqual(self.call('training.state',{'id':self.state['id']})['status'],'active')
        original=self.service.store.save
        def fail(*a):raise OSError('synthetic disk failure')
        self.service.store.save=fail
        with self.assertRaises(OSError):self.call('training.act',self.act_params('BUY'))
        self.service.store.save=original;state=self.call('training.state',{'id':self.state['id']});self.assertEqual(state['revision'],0);self.assertEqual(state['account']['cash'],100000)
    def test_original_source_unavailable_pauses_and_can_reveal(self):
        original=self.data.paths['raw'];self.data.paths['raw']=Path(self.folder.name)/'missing.sqlite'
        state=self.call('training.state',{'id':self.state['id']});self.assertEqual(state['status'],'paused');self.assertEqual(state['account']['nav'],100000)
        finished=self.call('training.finish',dict(id=state['id'],expected_revision=0,expected_phase='OPEN'));self.assertEqual(finished['status'],'revealed');self.assertEqual(finished['reveal']['name'],'名称暂不可读')
        self.assertFalse(self.data.paths['raw'].exists());self.data.paths['raw']=original
    def test_damaged_unseen_fields_pause_at_close_and_can_finish(self):
        for field,value in [('high',None),('low',None),('vol_lot',None),('high','bad'),('vol_lot',float('inf'))]:
            with self.subTest(field=field,value=value):
                # Fresh independent store for each damaged future CLOSE field.
                with writable(self.data.paths['raw']) as c:
                    c.execute("UPDATE equity_daily_raw SET high=11,low=9,vol_lot=100 WHERE trade_date='2024-01-02'")
                state=self.call('training.start',{'length':60})
                self.assertEqual(state['status'],'active');self.assertEqual(set(state['quotes']),{'open'})
                # Only damage after creation: complete-window sampling rejects
                # already-broken rows, but unseen source edits cannot leak at O.
                with writable(self.data.paths['raw']) as c:c.execute(f"UPDATE equity_daily_raw SET {field}=? WHERE trade_date='2024-01-02'",(value,))
                state=self.call('training.act',dict(id=state['id'],expected_revision=0,expected_phase='OPEN',action='HOLD'))
                self.assertEqual((state['status'],state['revision'],state['phase']),('paused',1,'CLOSE'))
                self.assertEqual(len(state['actions']),1);self.assertEqual(state['account']['nav'],100000)
                done=self.call('training.finish',dict(id=state['id'],expected_revision=1,expected_phase='CLOSE'))
                self.assertEqual(done['status'],'revealed');self.assertTrue(done['restricted'])
    def test_slippage_private_point_and_benchmark_match(self):
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_daily_raw SET open=10.99 WHERE trade_date='2024-01-02'")
        self.state=self.call('training.start',{'length':60,'slippage_bps':100})
        state=self.call('training.act',self.act_params('BUY'))
        expected=11*100/10.5
        self.assertAlmostEqual(state['last_receipt']['price'],expected)
        for key in ('raw_price','multiplier','rule','gate','point_hash','history_hash','background_hash'):
            self.assertNotIn(key,state)
        with self.service.store.transaction() as c:
            stored=self.service.store.get(c,state['id'])
            self.assertEqual(stored['account'],stored['benchmark'])

if __name__=='__main__':unittest.main()
