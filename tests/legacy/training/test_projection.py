import unittest,copy,json,tempfile,sqlite3
from contextlib import contextmanager
from pathlib import Path
from guanlan_app.features.training.projection import project
from guanlan_domain.training.rules import digest

@contextmanager
def writable(path):
    c=sqlite3.connect(path)
    try:
        with c:yield c
    finally:c.close()

class FakeData:
    def __init__(self,path):
        self.paths={'raw':path};self.current={'open':10,'high':11,'low':9,'close':10.5,'factor':1,'pre_close':10,'vol_lot':100}
    def background(self,*args):
        from datetime import date,timedelta
        return [dict(trade_date=(date(2023,1,1)+timedelta(days=i)).isoformat(),open=10,high=11,low=9,close=10,vol_lot=100,factor=1) for i in range(120)]
    def point(self,code,date,phase):
        row={k:v for k,v in self.current.items() if phase=='CLOSE' or k in ('open','pre_close','factor')}
        return {'row':row,'gate':{'buy':'','sell':'','valid':True},'rule':{'kind':'limited','origin':'derived'},'fingerprint':digest(row)}

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'synthetic.sqlite'
        with writable(self.path) as c:
            c.executescript('CREATE TABLE equity_daily_raw(ts_code,trade_date,open,high,low,close,vol_lot); CREATE TABLE equity_adj_factor(ts_code,trade_date,adj_factor);')
            c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?)',('SECRET','2024-01-02',10,11,9,10.5,100));c.execute('INSERT INTO equity_adj_factor VALUES(?,?,?)',('SECRET','2024-01-02',1))
        self.data=FakeData(self.path);self.session=dict(code='SECRET',start='2024-01-02',date='2024-01-02',phase='OPEN',day=1)
    def tearDown(self):self.folder.cleanup()
    def test_open_future_invariance_all_periods(self):
        for period in ('daily','weekly','monthly'):
            a=project(self.data,self.session,period=period)
            self.data.current.update(high=99,low=1,close=75,vol_lot=999999)
            with writable(self.path) as c:c.execute('UPDATE equity_daily_raw SET high=99,low=1,close=75,vol_lot=999999')
            b=project(self.data,self.session,period=period)
            self.assertEqual(a,b);self.assertEqual(set(b['quotes']),{'open'});self.assertEqual(b['opening']['price'],100)
            public={k:v for k,v in b.items() if not k.endswith('_hash')}
            self.assertNotIn('SECRET',json.dumps(public));self.assertNotIn('2024-01-02',json.dumps(public))
    def test_close_future_and_factor_invariance(self):
        self.session['phase']='CLOSE';a=project(self.data,self.session)
        with writable(self.path) as c:
            c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?)',('SECRET','2024-01-03',999,999,999,999,99999));c.execute('INSERT INTO equity_adj_factor VALUES(?,?,?)',('SECRET','2024-01-03',999))
        self.assertEqual(a,project(self.data,self.session));self.assertIsNone(a['opening']);self.assertEqual(len(a['bars']),121)
    def test_adjusted_price_domain(self):
        self.data.current.update(open=5,high=5.5,low=4.5,close=5.25,factor=2)
        self.assertEqual(project(self.data,self.session)['price'],100)

if __name__=='__main__':unittest.main()
