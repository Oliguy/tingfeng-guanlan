import copy
import json
import os
import sqlite3
from datetime import date, timedelta
from unittest.mock import patch
from tests.legacy.observer_collections.test_quotes import QuoteReferences; from tests.legacy.observer_collections.test_quotes import writable
import guanlan_data.repositories.observer_collections.member_signals as signals
from guanlan_data.repositories.observer_collections.quotes import bars
from guanlan_domain.observer_math.signals import strength
from guanlan_data.repositories.industry_index.inputs import digest
import guanlan_data.repositories.observer_collections.quotes as quotes


class MemberSignals(QuoteReferences):
    def setUp(self):
        super().setUp()
        import guanlan_data.repositories.observer_series.sources as sources
        from guanlan_data.repositories.observer_series.build import build
        from guanlan_data.repositories.observer_calendar import load
        self.series_paths={**sources.paths(),'stock':self.db,'support':self.support,'etf':self.db.with_name('etf.sqlite'),'results':self.db.with_name('results.sqlite')}
        raw=self.raw_group();refs={digest(q['ref']):q['ref'] for q in raw['quotes'].values()}
        with patch('guanlan_data.repositories.observer_series.build.sources.active_references',return_value=refs):
            result=build(configured=self.series_paths,kinds=('stock',),job_id='members')
            self.assertFalse(result['failed']);self.assertFalse(result['invalid_references'])
        configured=patch.object(signals,'configured',return_value=self.series_paths)
        configured.start();self.addCleanup(configured.stop)

    def raw_group(self):
        refs = quotes.references(self.m, self.db, self.support)
        return {'publication_id': 'p1', 'header': {'actual_end': self.ref['end']},
                'group': {'id': 'I01', 'members': [{'code': c} for c in refs]}, 'quotes': refs}

    def test_batch_reuses_connection_and_calendar_without_changing_values(self):
        raw = self.raw_group()
        expected = {c: quotes.load(q['ref']) for c, q in raw['quotes'].items()}
        statements = []
        with quotes.read_scope() as scope:
            connection = scope.connection(self.db)
            connection.set_trace_callback(statements.append)
            actual = {c: quotes.load(q['ref']) for c, q in raw['quotes'].items()}
            self.assertEqual(len(scope.connections), 1)
            self.assertEqual(len(scope.calendars), 1)
            self.assertEqual(actual, expected)
        # The full day states (including closed days) are read once or reused from the immutable cache.
        self.assertLessEqual(sum('SELECT cal_date,is_open FROM trade_calendar' in sql for sql in statements), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_shared_receipt_checked_once_and_tampering_rejected(self):
        raw = self.raw_group()
        day = self.ref['end']
        values = [{'ts_code': c, 'trade_date': day, 'adj_factor': rows[day]['adj_factor']}
                  for c, rows in self.m['quotes'].items()]
        checksum = digest(values)
        with writable(self.support) as connection:
            connection.execute('CREATE TABLE responses(request_id TEXT,endpoint TEXT,rows TEXT,checksum TEXT)')
            connection.execute('INSERT INTO responses VALUES(?,?,?,?)',
                               ('shared-factor', 'adj_factor', json.dumps(values), checksum))
        with writable(self.db) as connection:
            connection.execute('DELETE FROM equity_adj_factor WHERE trade_date=?', (day,))
        for q in raw['quotes'].values():
            q['ref']['support_requests'] = [['shared-factor', checksum]]
        with quotes.read_scope() as scope, patch.object(quotes, 'digest', wraps=digest) as check:
            for c, q in raw['quotes'].items():
                payload = quotes.load(q['ref'])
                self.assertEqual(payload['rows'][-1]['adj_factor'], self.m['quotes'][c][day]['adj_factor'])
            self.assertEqual(len(scope.connections), 2)
            self.assertEqual(len(scope.receipts), 1)
            self.assertEqual(sum(call.args[0] == values for call in check.call_args_list), 1)
        altered = copy.deepcopy(values);altered[0]['adj_factor'] = 99
        with writable(self.support) as connection:
            connection.execute('UPDATE responses SET rows=?', (json.dumps(altered),))
        with self.assertRaisesRegex(ValueError, '补充行情来源已修订'):
            quotes.load(raw['quotes'][self.code]['ref'])

    def test_source_change_during_scope_is_rejected_and_connections_closed(self):
        connection = None
        with self.assertRaisesRegex(ValueError, '来源发生变化'):
            with quotes.read_scope() as scope:
                connection = scope.connection(self.db)
                quotes.load(self.ref)
                original = self.db.stat()
                os.utime(self.db, ns=(original.st_atime_ns, original.st_mtime_ns + 1000000))
                quotes.load(self.ref)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
        self.assertIsNone(quotes._scope.get())

    def test_group_source_revision_and_mid_batch_change_fence(self):
        raw = self.raw_group()
        before = signals.source_revision(raw)
        result = signals.members(raw)
        self.assertEqual(result['source_revision'], before)
        self.assertEqual(result['data_revision'], 'p1')
        with patch.object(signals.Reader, 'assert_unchanged', side_effect=ValueError('读取期间行情来源发生变化')):
            with self.assertRaisesRegex(ValueError, '来源发生变化'):
                signals.members(raw)
        with writable(self.db) as connection:
            connection.execute('UPDATE equity_adj_factor SET adj_factor=2 WHERE trade_date=?', (self.ref['end'],))
        self.assertNotEqual(signals.source_revision(raw), before)
        self.assertEqual(signals.members(raw)['items'][self.code]['status'], 'unavailable')

    def test_shared_algorithm_and_strict_equality(self):
        weeks, _, _ = bars(self.ref, self.ref['start'], 'weekly')
        result = signals.signal(self.ref, self.ref['end'])
        expected = strength(weeks)
        for field in ('above_now', 'strength_score', 'consecutive_above_weeks', 'distance_30w', 'two_weeks_above'):
            self.assertEqual(result[field], expected[field])
        self.assertFalse(result['above_now'])
        self.assertEqual(result['status'], 'ready')

    def test_cached_result_does_not_hide_historical_revision(self):
        result = signals.signal(self.ref, self.ref['end'])
        self.assertEqual(result['status'], 'ready')
        with writable(self.db) as connection:
            connection.execute('UPDATE equity_daily_raw SET close=99 WHERE ts_code=? AND trade_date=?',
                               (self.code, self.m['dates'][-20]))
        revised = signals.signal(self.ref, self.ref['end'])
        self.assertEqual(revised['status'], 'unavailable')
        self.assertIn('修订', revised['reason'])

    def test_stale_quote_is_not_current(self):
        tomorrow = (date.fromisoformat(self.ref['end']) + timedelta(days=1)).isoformat()
        result = signals.signal(self.ref, tomorrow)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['quote_date'], self.ref['end'])

    def test_missing_persisted_binding_is_terminal_not_zero_strength(self):
        with writable(self.series_paths['results']) as c:c.execute('DELETE FROM series_bindings WHERE ref_id=?',(digest(self.ref),))
        with patch.object(quotes,'load',side_effect=AssertionError('browsing calculated history')):
            result = signals.signal(self.ref, self.ref['end'])
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['strength_score'])

    def test_cache_returns_independent_values(self):
        first = signals.signal(self.ref, self.ref['end'])
        first['above_now'] = True
        second = signals.signal(self.ref, self.ref['end'])
        self.assertFalse(second['above_now'])

    def test_fast_projection_matches_chart_on_varying_prices(self):
        from guanlan_domain.observer_math.indicators import chart_rows
        from guanlan_domain.observer_math.signals import price_strength
        import math
        start = date(2024, 1, 1)
        daily = []
        for i in range(700):
            day = start + timedelta(days=i)
            if day.weekday() >= 5 or 400 <= i < 414:
                continue
            value = 100 + math.sin(i / 20) * 20 + i / 10
            daily.append({'trade_date': day.isoformat(), 'open': value, 'high': value + 1,
                          'low': value - 1, 'close': value, 'volume': 10, 'amount': 1000})
        for n in (0, 1, 100, 151, 200, len(daily)):
            self.assertEqual(price_strength(daily[:n]), strength(chart_rows(daily[:n], 'weekly')))

    def test_window_validation_keeps_missing_nonfinite_and_boolean_semantics(self):
        from guanlan_domain.observer_math.indicators import chart_rows
        from guanlan_domain.observer_math.signals import price_strength
        start = date(2024, 1, 1)
        daily = [{'trade_date': (start + timedelta(weeks=i)).isoformat(), 'open': 10,
                  'high': 11, 'low': 9, 'close': 10 + i / 10, 'volume': 1, 'amount': 10}
                 for i in range(100)]
        for invalid in (None, float('nan'), True):
            for index in (5, 65, 99):
                rows = copy.deepcopy(daily);rows[index]['close'] = invalid
                self.assertEqual(price_strength(rows), strength(chart_rows(rows, 'weekly')))
