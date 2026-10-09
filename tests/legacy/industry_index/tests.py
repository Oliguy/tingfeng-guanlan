"""Financial invariants and failure paths with hand-checkable fixtures."""
import copy
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from guanlan_domain.industry_index.engine import calculate_group; from guanlan_domain.industry_index.engine import calculate
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import year_before
from guanlan_data.repositories.industry_index.store import publish; from guanlan_data.repositories.industry_index.store import query; from guanlan_data.repositories.industry_index.store import writer_lock
from guanlan_app.features.industry_index.service import build
from guanlan_data.repositories.industry_index.support import valuation_quotes; from guanlan_data.repositories.industry_index.support import EvidenceClient; from guanlan_data.repositories.industry_index.support import repair_inputs
from guanlan_domain.industry_index.indicators import enrich; from guanlan_domain.industry_index.indicators import calculator


def fixture(days=4):
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(21 + days)]
    codes = ['000001.SZ', '600001.SH']
    quotes = {}
    for code, close, volume in zip(codes, [10., 1000.], [1., 4.]):
        quotes[code] = {d: dict(open=close, high=close, low=close, close=close,
                               adj_factor=1., vol_lot=volume, amount_thousand_cny=2.) for d in dates}
    group = dict(id='I01', name='fixture', members=[dict(code=code, name=code, status='source_supported', relations=[]) for code in codes])
    market = dict(dates=dates, display_dates=dates[21:], quotes=quotes,
                  metadata={c: dict(list_status='L', list_date='20200101') for c in codes})
    return group, market


class MathTests(unittest.TestCase):
    def test_equal_return_not_equal_price(self):
        g, m = fixture(1); day = m['display_dates'][0]
        m['quotes']['000001.SZ'][day].update(open=10., high=12., low=10., close=12.)
        m['quotes']['600001.SH'][day].update(open=1000., high=1000., low=900., close=900.)
        r = calculate_group(g, m); p = r['points'][0]
        self.assertAlmostEqual(p['close'], 1050)
        self.assertAlmostEqual(p['daily_return'], .05)
        self.assertAlmostEqual(p['high'], 1100)
        self.assertAlmostEqual(p['low'], 950)
        self.assertEqual(p['open'], 1000)
        self.assertEqual(p['volume'], 500)
        self.assertEqual(p['amount'], 4000)
        self.assertEqual(p['mean_volume'], 250)

    def test_split_is_not_fifty_percent_loss(self):
        g, m = fixture(1); d = m['display_dates'][0]
        m['quotes']['000001.SZ'][d].update(open=5., high=5., low=5., close=5., adj_factor=2.)
        p = calculate_group(g, m)['points'][0]
        self.assertEqual(p['close'], 1000)
        self.assertEqual(p['volume'], 500)  # Volume is not multiplied by price factors.

    def test_unknown_missing_never_zero_renormalized_or_restarted(self):
        g, m = fixture(5); d = m['display_dates'][1]
        del m['quotes']['000001.SZ'][d]
        r = calculate_group(g, m)
        self.assertEqual([p['status'] for p in r['points']], ['ok', 'gap', 'gap', 'gap', 'gap'])
        self.assertIsNone(r['points'][1]['close'])
        self.assertEqual(r['points'][1]['coverage'], .5)
        self.assertEqual(r['points'][1]['missing'][0]['reason'], 'MISSING_CURRENT_SESSION')
        self.assertIsNone(r['points'][3]['close'])
        self.assertEqual(r['stats']['segments'], 1)
        self.assertIsNone(r['stats']['year_return'])

    def test_relative_volume_uses_previous_twenty_not_today(self):
        g, m = fixture(1); d = m['display_dates'][0]
        for rows in m['quotes'].values(): rows[d]['vol_lot'] = 2.
        self.assertEqual(calculate_group(g, m)['points'][0]['relative_volume_20'], 1.25)
        del m['quotes']['000001.SZ'][m['dates'][1]]
        p = calculate_group(g, m)['points'][0]
        self.assertIsNone(p['relative_volume_20'])
        self.assertEqual(p['relative_volume_coverage'], .5)
        self.assertEqual(p['status'], 'ok')  # Volume warmup gap does not erase valid price.

    def test_new_listing_first_day_excluded_then_included(self):
        g, m = fixture(2)
        m['metadata']['000001.SZ']['list_date'] = m['display_dates'][0]
        r = calculate_group(g, m)['points']
        self.assertEqual([p['expected_members'] for p in r], [1, 2])
        self.assertEqual(r[0]['pre_listing_members'], 1)

    def test_current_delisted_and_identity_unknown_excluded_transparently(self):
        g, m = fixture(1)
        m['metadata']['000001.SZ']['list_status'] = 'D'
        r = calculate_group(g, m)
        self.assertEqual(r['points'][0]['expected_members'], 1)
        self.assertEqual(r['excluded'][0]['reason'], 'NOT_CURRENTLY_LISTED')
        m['metadata']['000001.SZ'] = None
        r = calculate_group(g, m)
        self.assertEqual(r['points'][0]['expected_members'], 1)
        self.assertEqual(r['stats']['unknown_identity_members'], 1)
        self.assertEqual(r['excluded'][0]['reason'], 'SECURITY_IDENTITY_UNKNOWN')
        self.assertIsNone(r['stats']['year_return'])

    def test_duplicates_do_not_overweight_and_multi_membership_retained(self):
        g, m = fixture(1); baseline = calculate_group(g, m)
        g['members'].append(copy.deepcopy(g['members'][0]))
        self.assertEqual(calculate_group(g, m), baseline)
        g2 = {**g, 'id': 'I02'}
        results = calculate({'groups': [g, g2]}, m)
        self.assertEqual([p['stats']['classified_members'] for p in results], [2, 2])

    def test_empty_group_not_zero_index(self):
        g, m = fixture(1); g['members'] = []
        p = calculate_group(g, m)['points'][0]
        self.assertEqual(p['status'], 'empty'); self.assertIsNone(p['close'])

    def test_bad_factor_and_nonfinite_prices_reported(self):
        for value in (None, 0, float('nan'), float('inf'), -1):
            g, m = fixture(1)
            m['quotes']['000001.SZ'][m['display_dates'][0]]['adj_factor'] = value
            p = calculate_group(g, m)['points'][0]
            self.assertEqual(p['status'], 'gap')
            json.dumps(p, allow_nan=False)

    def test_invalid_ohlc_and_invalid_volume_independent(self):
        g, m = fixture(1); bar = m['quotes']['000001.SZ'][m['display_dates'][0]]
        bar['high'] = 9.
        self.assertEqual(calculate_group(g, m)['points'][0]['missing'][0]['reason'], 'INVALID_OHLC_ORDER')
        bar['high'], bar['vol_lot'] = 10., -1.
        p = calculate_group(g, m)['points'][0]
        self.assertEqual(p['status'], 'ok'); self.assertIsNone(p['volume'])

    def test_zero_volume_baseline_is_not_infinite(self):
        g, m = fixture(1)
        for r in m['quotes']['000001.SZ'].values(): r['vol_lot'] = 0.
        p = calculate_group(g, m)['points'][0]
        self.assertIsNone(p['relative_volume_20']); self.assertEqual(p['volume'], 400.)

    def test_inputs_not_mutated(self):
        g, m = fixture(); before = copy.deepcopy((g, m))
        self.assertEqual(calculate_group(g, m), calculate_group(g, m))
        self.assertEqual((g, m), before)

    def test_leap_year_window(self):
        self.assertEqual(year_before('2024-02-29'), '2023-02-28')

    def test_confirmed_halt_stays_in_denominator_and_resumes_with_split(self):
        g,m=fixture(4); a,b,c,d=m['display_dates']; code='000001.SZ'
        del m['quotes'][code][a]; del m['quotes'][code][b]
        m['suspensions']={code:{a:'evidence-a',b:'evidence-b'}}
        m['quotes'][code][c].update(open=6.,high=6.,low=6.,close=6.,adj_factor=2.)
        m['quotes'][code][d].update(open=6.,high=6.,low=6.,close=6.,adj_factor=2.)
        m['quotes']['600001.SH'][a].update(open=1100.,high=1100.,low=1100.,close=1100.)
        for day in (b,c,d):m['quotes']['600001.SH'][day].update(open=1100.,high=1100.,low=1100.,close=1100.)
        original=copy.deepcopy(m); m['quotes']=valuation_quotes(m)
        r=calculate_group(g,m)['points']
        self.assertAlmostEqual(r[0]['close'],1050.)
        self.assertEqual(r[1]['close'],r[0]['close'])
        self.assertAlmostEqual(r[2]['close'],1155.)
        self.assertEqual(r[0]['volume'],400.)
        self.assertEqual(r[0]['suspended_members'],1)
        self.assertNotIn(a,original['quotes'][code])

    def test_missing_without_full_day_evidence_not_carried(self):
        g,m=fixture(2); day=m['display_dates'][0]; code='000001.SZ'
        del m['quotes'][code][day]
        m['quotes']=valuation_quotes(m)
        self.assertNotIn(day,m['quotes'][code])
        self.assertEqual(calculate_group(g,m)['points'][0]['status'],'gap')

    def test_indicator_parity_and_display_crop_not_reseeded(self):
        g,m=fixture(300)
        m['calculation_dates']=m['display_dates'][:]
        m['display_dates']=m['display_dates'][-80:]
        for i,day in enumerate(m['dates']):
            r=m['quotes']['000001.SZ'][day];v=10+i*.1;r.update(open=v,high=v,low=v,close=v)
        raw=calculate_group(g,m); expected=calculator().chart_rows(raw['calculation_points'],'daily')[-80:]
        result=enrich(raw)
        for row,exp in zip(result['points'],expected):
            for field in ['z_zhixing_short_trend','z_zhixing_bull_bear','thirty_week_ma','bbi']:self.assertEqual(row[field],exp[field])
        self.assertTrue(all(r['thirty_week_ma'] is not None for r in result['points']))
        last_before=copy.deepcopy(result['points'][-2])
        day=m['dates'][-1];r=m['quotes']['000001.SZ'][day];r.update(open=1000.,high=1000.,low=1000.,close=1000.)
        self.assertEqual(enrich(calculate_group(g,m))['points'][-2],last_before)


class StoreTests(unittest.TestCase):
    def setUp(self):
        module = Path(__file__).resolve().parents[1]
        config = json.loads((module / 'runtime.json').read_text(encoding='utf-8-sig'))
        test_root = (module / config['test_root']).resolve()
        test_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='industry30-', dir=test_root)
        assert Path(self.tmp.name).resolve().is_relative_to(test_root)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'result.sqlite'
        g, m = fixture(1); self.groups = [calculate_group(g, m)]

    def test_idempotent_and_failure_keeps_previous_run(self):
        publish(self.path, {'run_id': 'a'}, self.groups)
        self.assertTrue(publish(self.path, {'run_id': 'a'}, self.groups)['idempotent'])
        def fail(): raise RuntimeError('injected-before-commit')
        with self.assertRaises(RuntimeError): publish(self.path, {'run_id': 'b'}, self.groups, before_commit=fail)
        self.assertEqual(query(self.path)['run_id'], 'a')
        with readonly(self.path) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM industry_runs').fetchone()[0], 1)
        publish(self.path, {'run_id': 'b'}, self.groups)
        self.assertEqual(query(self.path, run_id='a', view='detail', industry='I01')['run_id'], 'a')

    def test_same_run_different_content_rejected(self):
        publish(self.path, {'run_id': 'a'}, self.groups)
        self.groups[0]['name'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'NONDETERMINISTIC'): publish(self.path, {'run_id': 'a'}, self.groups)
        self.assertEqual(query(self.path)['groups'][0]['name'], 'fixture')

    def test_readonly_and_schema_guard(self):
        publish(self.path, {'run_id': 'a'}, self.groups)
        with readonly(self.path) as c:
            with self.assertRaises(sqlite3.OperationalError): c.execute("DELETE FROM industry_runs")
        with self.assertRaisesRegex(ValueError, 'INVALID_INDUSTRY'): query(self.path, view='detail', industry="I01' OR 1")
        foreign = Path(self.tmp.name) / 'business.sqlite'
        with closing(sqlite3.connect(foreign)) as c:
            c.execute('CREATE TABLE facts(x)'); c.commit()
        with self.assertRaisesRegex(ValueError, 'NOT_AN_INDUSTRY'): publish(foreign, {'run_id': 'a'}, self.groups)

    def test_never_overwrite_input(self):
        self.path.touch()
        with self.assertRaisesRegex(ValueError, 'OUTPUT_MUST_NOT_BE_INPUT'):
            build(self.path, self.path, self.path, end_date='2026-09-29')

    def test_writer_lock_released(self):
        with writer_lock(self.path):
            with self.assertRaisesRegex(ValueError, 'UPDATE_ALREADY_RUNNING'):
                with writer_lock(self.path): pass
        with writer_lock(self.path): pass

    def test_support_repairs_identity_and_daily_but_not_intraday_halts(self):
        g,m=fixture(2); code='000001.SZ';day=m['display_dates'][0]; m['metadata'][code]=None
        del m['quotes'][code][day]; m['market_hash']='fixture'
        class Frame:
            def __init__(self,rows):self.rows=rows
            def to_json(self,**kw):return json.dumps(self.rows)
        class Provider:
            def query(self,endpoint,**params):
                if endpoint=='stock_basic':return Frame([dict(ts_code=code,list_status='L',list_date='20200101')])
                if endpoint=='suspend_d':return Frame([dict(ts_code=code,trade_date=day.replace('-',''),suspend_type='S',suspend_timing='09:30-10:00')])
                if endpoint=='daily':return Frame([dict(ts_code=code,trade_date=day.replace('-',''),open=11.,high=11.,low=11.,close=11.,vol=2.,amount=3.)])
                return Frame([dict(ts_code=code,trade_date=day.replace('-',''),adj_factor=1.)])
        cache=Path(self.tmp.name)/'support.sqlite'
        repair_inputs(m,cache,provider=Provider())
        self.assertEqual(m['support']['identity_repairs'],[code])
        self.assertNotIn(day,m['suspensions'].get(code,{}))
        self.assertEqual(m['quotes'][code][day]['close'],11.)
        self.assertEqual(m['quotes'][code][day]['vol_lot'],2.)
        self.assertAlmostEqual(calculate_group(g,m)['points'][0]['close'],1050.)
        replay=EvidenceClient(cache,online=False)
        rows=replay.request('stock_basic',ts_code=code,fields='ts_code,name,list_status,list_date,delist_date')
        self.assertEqual(rows[0]['list_date'],'20200101')
        with self.assertRaisesRegex(ValueError,'NOT_CACHED'):
            replay.request('daily',ts_code=code,start_date='19990101',end_date='19990102')

    def test_support_rejects_truncation_and_wrong_security(self):
        class Frame:
            def __init__(self,rows):self.rows=rows
            def to_json(self,**kw):return json.dumps(self.rows)
        class Provider:
            def __init__(self,rows):self.rows=rows
            def query(self,*a,**kw):return Frame(self.rows)
        cache=Path(self.tmp.name)/'support.sqlite'
        client=EvidenceClient(cache,Provider([{'ts_code':'000001.SZ'}]*5000))
        with self.assertRaisesRegex(ValueError,'TRUNCATED'):client.request('stock_basic',ts_code='000001.SZ')
        client=EvidenceClient(cache,Provider([{'ts_code':'600001.SH'}]))
        with self.assertRaisesRegex(ValueError,'UNEXPECTED_SECURITY'):client.request('stock_basic',ts_code='000001.SZ')


if __name__ == '__main__': unittest.main()
