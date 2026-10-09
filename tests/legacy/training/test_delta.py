import json,tempfile,unittest,uuid
from datetime import date,timedelta
from guanlan_app.features.training.service import Service
from tests.legacy.training.test_service import fixture; from tests.legacy.training.test_service import writable
from guanlan_app.features.training.projection import project
from guanlan_domain.observer_math.indicators import chart_rows; from guanlan_domain.observer_math.indicators import apply_overlays
from guanlan_domain.observer_calendar.core import Calendar

class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.data=fixture(self.folder.name)
        # Sufficient synthetic initialization history and explicit closed dates.
        with writable(self.data.paths['raw']) as c:
            first=date(2022,4,1)
            for i in range(180):
                day=first+timedelta(days=i)
                if day.weekday()<5:
                    d=day.isoformat();c.execute('INSERT INTO equity_daily_raw VALUES(?,?,?,?,?,?,?,?)',('600001.SH',d,10,11,9,10.5,10,100));c.execute('INSERT INTO equity_adj_factor VALUES(?,?,?)',('600001.SH',d,1));c.execute('INSERT INTO trade_calendar VALUES(?,?,?)',('SSE',d,1))
            d=first
            while d<date(2025,1,1):
                c.execute('INSERT OR IGNORE INTO trade_calendar VALUES(?,?,?)',('SSE',d.isoformat(),0));d+=timedelta(days=1)
        self.service=Service(self.data);self.state=self.call('training.start',{'length':150,'response_mode':'delta'})
    def tearDown(self):self.service.close();self.folder.cleanup()
    def call(self,op,params,request=None):return self.service.invoke(op,params,request or str(uuid.uuid4()))
    def act(self,action):
        old=self.state;params=dict(id=old['id'],expected_revision=old['revision'],expected_phase=old['phase'],action=action,response_mode='delta')
        result=self.call('training.act',params)
        self.assertEqual(result['response_kind'],'delta');self.assertEqual(result['base_revision'],old['revision']);self.assertNotIn('bars',result);self.assertNotIn('actions',result)
        bars=old['bars'];delta=result['bar_delta']
        if delta:bars=bars[:delta['remove_from']]+delta['append'];self.assertEqual(len(bars),delta['total'])
        self.state={**old,**result,'bars':bars,'actions':old['actions']+result['action_delta']}
        return result
    def test_four_rounds_incremental_and_reopen(self):
        self.assertEqual(len(self.state['bars']),120)
        self.act('BUY');self.assertEqual(self.state['phase'],'OPEN');self.assertEqual(len(self.state['bars']),120)
        r=self.act('HOLD');self.assertEqual(len(r['bar_delta']['append']),1);self.assertEqual(self.state['phase'],'CLOSE')
        self.act('HOLD');self.assertEqual((self.state['day'],self.state['phase']),(2,'OPEN'));self.assertEqual(set(self.state['quotes']),{'open'})
        self.act('SELL');self.assertEqual(self.state['phase'],'OPEN')
        self.act('HOLD');self.assertEqual(self.state['round'],4)
        reopened=self.call('training.state',{'id':self.state['id'],'response_mode':'delta'})
        self.assertEqual(reopened['bars'],self.state['bars']);self.assertEqual(reopened['action_count'],5);self.assertEqual(len(reopened['actions']),2)
        history=self.call('training.history',{'id':self.state['id'],'offset':1,'limit':2});self.assertEqual(len(history['items']),2);self.assertEqual(history['total'],5)
    def test_lines_reuse_exact_existing_algorithms_and_no_identity(self):
        with self.service.store.transaction() as c:s=self.service.store.get(c,self.state['id'])
        window=self.data.prepare(s);rows=window.initialization(s['date'],s['phase']);anchor=rows[-1]['close']*rows[-1]['factor']
        normalized=[{'trade_date':r['trade_date'],**{k:r[k]*r['factor']*100/anchor for k in ('open','high','low','close')},'volume':0,'amount':0} for r in rows]
        for period in ('daily','weekly'):
            actual=project(self.data,s,period=period)['bars'][-1];expected=chart_rows(normalized,period,Calendar(window.calendar_days))[-1]
            for field in ('z_zhixing_short_trend','z_zhixing_bull_bear','bbi','thirty_week_ma'):self.assertAlmostEqual(actual[field],expected[field])
        self.assertNotIn('600001',json.dumps(self.state));self.assertNotIn('2024-01-02',json.dumps(self.state));self.assertNotIn('合成测试',json.dumps(self.state))
        self.assertIsNotNone(self.state['bars'][-1]['thirty_week_ma'])
    def test_unseen_changes_do_not_change_open_indicators_all_periods(self):
        before={p:self.call('training.state',{'id':self.state['id'],'period':p,'response_mode':'delta'})['bars'] for p in ('daily','weekly','monthly')}
        with writable(self.data.paths['raw']) as c:c.execute("UPDATE equity_daily_raw SET high=15,low=5,close=10.8,vol_lot=999 WHERE trade_date='2024-01-02'")
        for p in before:
            result=self.call('training.state',{'id':self.state['id'],'period':p,'response_mode':'delta'})
            self.assertEqual(result['status'],'active');self.assertEqual(result['bars'],before[p]);self.assertEqual(set(result['quotes']),{'open'})
    def test_warm_actions_do_not_select_market_rows(self):
        from unittest.mock import patch
        with patch('guanlan_data.repositories.training.cache.readonly',side_effect=AssertionError('unexpected market row read')):
            self.act('BUY');self.act('HOLD');self.act('HOLD');self.act('SELL')
    def test_monthly_30_week_line_uses_last_known_day_and_streamed_days_match(self):
        from decimal import Decimal
        for _ in range(6):
            with self.service.store.transaction() as c:s=self.service.store.get(c,self.state['id'])
            window=self.data.prepare(s);rows=window.initialization(s['date'],s['phase'])
            anchor=Decimal(str(window.background_rows[-1]['close']))*Decimal(str(window.background_rows[-1]['factor']))
            normalized=[{'trade_date':r['trade_date'],**{k:float(Decimal(100)*Decimal(str(r[k]))*Decimal(str(r['factor']))/anchor) for k in ('open','high','low','close')},'volume':0,'amount':0} for r in rows]
            daily=chart_rows(normalized,'daily',Calendar(window.calendar_days));months=[]
            for r in daily:
                if not months or months[-1]['trade_date'][:7]!=r['trade_date'][:7]:months.append(dict(r))
                else:months[-1].update(trade_date=r['trade_date'],close=r['close'],thirty_week_ma=r['thirty_week_ma'])
            monthly=apply_overlays(months,{r['trade_date']:r['thirty_week_ma'] for r in months})
            for period,expected in [('daily',daily[-1]),('monthly',monthly[-1])]:
                actual=project(self.data,s,period=period)['bars'][-1]
                for field in ('z_zhixing_short_trend','z_zhixing_bull_bear','thirty_week_ma','bbi'):self.assertEqual(actual[field],expected[field])
            self.act('HOLD')

if __name__=='__main__':unittest.main()
