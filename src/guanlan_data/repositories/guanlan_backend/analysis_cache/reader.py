"""Pinned SELECT queries and bounded, aligned, read-only NumPy columns."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
import duckdb
import numpy as np
from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import COLUMNS; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import DEFAULT_ARRAY_LIMIT; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import PRICE_COLUMNS; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import SCHEMA_VERSION; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import UNITS; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import VALUE_COLUMNS; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import default_paths; from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import iso_date
from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import CacheError; from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import FileLock; from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import directory_bytes; from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import generation_entries; from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import owned_path; from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import read_json; from guanlan_data.repositories.guanlan_backend.analysis_cache.storage import sha256

def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"

@dataclass(frozen=True)
class ArrayResult:
    columns: dict[str, np.ndarray]
    metadata: dict

    def __getitem__(self, column):
        return self.columns[column]

    def __len__(self):
        return len(self.columns['trade_date'])

class AnalysisCache:

    def __init__(self, root: str | Path | None=None):
        self.root = Path(root or default_paths()[1]).resolve()
        self._connection = None
        self._pin = None
        try:
            for _ in range(3):
                pointer_path = self.root / 'active.json'
                if not pointer_path.exists():
                    raise CacheError('No verified analysis generation; run analysis-cache build')
                active = read_json(pointer_path)
                mp = owned_path(self.root, active['manifest'])
                try:
                    self._pin = FileLock(mp.with_suffix('.lock'), shared=True)
                    self.manifest = read_json(mp)
                    if sha256(mp) != active['sha256']:
                        raise CacheError('Pinned manifest identity differs')
                    break
                except (FileNotFoundError, CacheError):
                    if self._pin:
                        self._pin.close()
                        self._pin = None
            else:
                raise CacheError('Generation changed while opening; retry')
            if self.manifest['schema_version'] != SCHEMA_VERSION or self.manifest['generation_id'] != active['generation_id']:
                raise CacheError('Unsupported or inconsistent cache version')
            for entry in generation_entries(self.manifest):
                path = owned_path(self.root, entry['path'])
                if path.stat().st_size != entry['bytes']:
                    raise CacheError(f"Cache file size differs: {entry['path']}")
        except BaseException:
            self.close()
            raise

    @property
    def connection(self):
        if self._connection is None:
            c = duckdb.connect(config={'memory_limit': '768MB', 'threads': 4, 'max_temp_directory_size': '0B', 'autoinstall_known_extensions': False, 'autoload_known_extensions': False, 'allow_community_extensions': False})
            try:
                files = [str(owned_path(self.root, e['path']).as_posix()) for e in generation_entries(self.manifest) if e['path'].endswith('.parquet')]
                file_list = '[' + ','.join((sql_literal(p) for p in files)) + ']'
                c.execute('SET allowed_paths = ' + file_list)
                c.execute('SET enable_external_access = false')
                partitions = '[' + ','.join((sql_literal(owned_path(self.root, e['path']).as_posix()) for e in self.manifest['partitions'].values())) + ']'
                c.execute(f'CREATE VIEW bars_basis AS SELECT * FROM read_parquet({partitions})')
                for view, key in (('anchors', 'anchors'), ('securities', 'symbols')):
                    p = sql_literal(owned_path(self.root, self.manifest[key]['path']).as_posix())
                    c.execute(f'CREATE VIEW {view} AS SELECT * FROM read_parquet({p})')
                projection = ['b.ts_code', 'b.trade_date'] + [f'b.{name}/a.anchor_factor AS {name}' if name in PRICE_COLUMNS else f'b.{name}' for name in VALUE_COLUMNS]
                c.execute('CREATE VIEW bars_qfq AS SELECT ' + ','.join(projection) + ',a.anchor_date,a.anchor_factor FROM bars_basis b JOIN anchors a USING(ts_code)')
                c.execute('CREATE VIEW bars AS SELECT * FROM bars_basis')
                c.execute('SET lock_configuration = true')
                self._connection = c
            except BaseException:
                c.close()
                raise
        return self._connection

    def metadata(self, adjustment='basis'):
        run = read_json(self.root / 'run.json') if (self.root / 'run.json').exists() else {}
        stale = bool(run.get('source_error') or (run.get('error') and run.get('generation_id') != self.manifest['generation_id']))
        units = dict(UNITS)
        if adjustment == 'qfq_latest':
            units.update({name: 'CNY, latest forward-adjusted display' for name in PRICE_COLUMNS})
        return {'generation_id': self.manifest['generation_id'], 'adjustment': adjustment, 'source_end': self.manifest['end'], 'source_revision_id': self.manifest['source']['revision_id'], 'source_snapshot': True, 'outdated': stale, 'last_update_error': run.get('source_error') or run.get('error'), 'units': units, 'missing_trading_dates': 'absent rows; no filling', 'anchor_policy': 'one latest factor per security in the pinned generation'}

    def query(self, sql: str, *, parameters=None, max_rows=10000):
        if not 0 < max_rows <= 1000000:
            raise CacheError('Query max_rows must be between 1 and 1000000')
        statements = self.connection.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise CacheError('Only one SELECT statement is accepted')
        cursor = self.connection.execute(sql, parameters or [])
        names = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise CacheError('Query result exceeds max_rows; add filters or raise the bounded limit')
        if len(json.dumps(rows, default=str)) > 32 * 1024 ** 2:
            raise CacheError('Query result exceeds 32 MiB; narrow selected columns')
        metadata = self.metadata('SQL_views')
        metadata['view_contract'] = {'bars': 'basis', 'bars_basis': 'basis', 'bars_qfq': 'qfq_latest'}
        metadata['units'] = {'bars_basis': self.metadata('basis')['units'], 'bars_qfq': self.metadata('qfq_latest')['units']}
        return {'columns': names, 'rows': rows, 'row_count': len(rows), 'metadata': metadata}

    def load(self, symbols=None, start=None, end=None, columns=None, adjustment='basis', *, max_bytes=DEFAULT_ARRAY_LIMIT):
        if adjustment not in ('basis', 'qfq_latest'):
            raise CacheError('Adjustment must be basis or qfq_latest')
        requested = list(columns) if columns is not None else list(VALUE_COLUMNS)
        if len(requested) != len(set(requested)) or any((c not in COLUMNS for c in requested)):
            raise CacheError('Unknown or duplicate array columns')
        selected = list(dict.fromkeys(['ts_code', 'trade_date'] + requested))
        clauses, params = ([], [])
        if symbols is not None:
            symbols = [symbols] if isinstance(symbols, str) else list(symbols)
            if any((not isinstance(s, str) or len(s) != 9 or s[6] != '.' or (not s[:6].isdigit()) for s in symbols)):
                raise CacheError('Symbols use six-digit code.exchange, e.g. 000001.SZ')
            clauses.append('ts_code IN (' + ','.join(('?' for _ in symbols)) + ')' if symbols else 'false')
            params += symbols
        if start:
            clauses.append('trade_date>=?')
            params.append(iso_date(start))
        if end:
            clauses.append('trade_date<=?')
            params.append(iso_date(end))
        if start and end and (iso_date(start) > iso_date(end)):
            raise CacheError('Start date is after end date')
        table = 'bars_basis' if adjustment == 'basis' else 'bars_qfq'
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        n = self.connection.execute(f'SELECT COUNT(*) FROM {table}{where}', params).fetchone()[0]
        estimate = n * (44 + 8 * (len(selected) - 2))
        if not 0 < max_bytes <= DEFAULT_ARRAY_LIMIT or estimate > max_bytes:
            raise CacheError(f'Array request needs {estimate} bytes; reduce securities, dates or columns')
        arrays = {name: np.empty(n, dtype='U9' if name == 'ts_code' else 'datetime64[D]' if name == 'trade_date' else 'float64') for name in selected}
        offset = 0
        for month, entry in sorted(self.manifest['partitions'].items()):
            if start and month < iso_date(start)[:7] or (end and month > iso_date(end)[:7]):
                continue
            file_expr = 'read_parquet(' + sql_literal(owned_path(self.root, entry['path']).as_posix()) + ') b'
            if adjustment == 'qfq_latest':
                file_expr += ' JOIN anchors a USING(ts_code)'
            projection = [f'b.{name}/a.anchor_factor AS {name}' if adjustment == 'qfq_latest' and name in PRICE_COLUMNS else f'b.{name}' for name in selected]
            sql = 'SELECT ' + ','.join(projection) + f' FROM {file_expr}{where} ORDER BY trade_date,ts_code'
            batches = self.connection.execute(sql, params).to_arrow_reader(32768)
            for batch in batches:
                size = batch.num_rows
                for name in selected:
                    arrays[name][offset:offset + size] = np.asarray(batch.column(name).to_pylist(), dtype='U9') if name == 'ts_code' else batch.column(name).to_numpy(zero_copy_only=False)
                offset += size
        if offset != n:
            raise CacheError('Pinned array count changed unexpectedly')
        for array in arrays.values():
            array.setflags(write=False)
        meta = self.metadata(adjustment)
        meta['array_bytes'] = sum((a.nbytes for a in arrays.values()))
        meta['ordering'] = 'trade_date, ts_code; dates ascend within each security'
        return ArrayResult(MappingProxyType(arrays), meta)

    def status(self, *, verify=False):
        if verify:
            for entry in generation_entries(self.manifest):
                if sha256(owned_path(self.root, entry['path'])) != entry['sha256']:
                    raise CacheError(f"Cache hash differs: {entry['path']}")
        return {**self.metadata(), 'root': str(self.root), 'rows': self.manifest['row_count'], 'symbols': self.manifest['symbol_count'], 'version_bytes': self.manifest['version_bytes'], 'directory_bytes': directory_bytes(self.root), 'source': self.manifest['source'], 'hashes_verified': verify}

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._pin is not None:
            self._pin.close()
            self._pin = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

def load(symbols=None, start=None, end=None, columns=None, adjustment='basis', *, root=None, max_bytes=DEFAULT_ARRAY_LIMIT):
    with AnalysisCache(root) as cache:
        return cache.load(symbols, start, end, columns, adjustment, max_bytes=max_bytes)
