"""Independent derived SQLite; immutable runs with an atomic current pointer."""
from guanlan_data import sqlite as database
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
from guanlan_data.repositories.industry_index.inputs import canonical; from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import readonly

SCHEMA = 'industry30.results.v1'


@contextmanager
def writer_lock(path):
    lock = Path(str(Path(path).resolve()) + '.build.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    # OS lock is released on process death; no stale PID lockfile to override.
    stream = open(lock, 'a+b')
    try:
        stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close(); raise ValueError('UPDATE_ALREADY_RUNNING') from exc
    try: yield
    finally: stream.close()


def publish(path, header, groups, *, before_commit=None, member_prices=None):
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    run_id = header['run_id']
    payload_hash = digest(groups if member_prices is None else [groups,member_prices])
    c = database.connect(target, timeout=5)
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and 'industry_meta' not in tables: raise ValueError('NOT_AN_INDUSTRY_RESULT_DATABASE')
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''CREATE TABLE IF NOT EXISTS industry_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS industry_runs(run_id TEXT PRIMARY KEY,header_json TEXT NOT NULL,
              content_hash TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS industry_groups(run_id TEXT NOT NULL,industry TEXT NOT NULL,
              summary_json TEXT NOT NULL,detail_json TEXT NOT NULL,
              PRIMARY KEY(run_id,industry), FOREIGN KEY(run_id) REFERENCES industry_runs(run_id));''')
        c.execute('''CREATE TABLE IF NOT EXISTS industry_member_prices(run_id TEXT NOT NULL,code TEXT NOT NULL,
            summary_json TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(run_id,code),
            FOREIGN KEY(run_id) REFERENCES industry_runs(run_id))''')
        version = c.execute("SELECT value FROM industry_meta WHERE key='schema'").fetchone()
        if version and version[0] != SCHEMA: raise ValueError('RESULT_SCHEMA_UNSUPPORTED')
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT content_hash FROM industry_runs WHERE run_id=?', (run_id,)).fetchone()
        if previous and previous[0] != payload_hash: raise ValueError('NONDETERMINISTIC_RUN_COLLISION')
        if not previous:
            c.execute('INSERT INTO industry_runs VALUES(?,?,?,?)',
                      (run_id, canonical(header), payload_hash, datetime.now(timezone.utc).isoformat()))
            for group in groups:
                summary = {k: group[k] for k in ('id', 'name', 'stats', 'high_low_kind', 'membership_mode', 'method_version')}
                summary['latest'] = group['points'][-1] if group['points'] else None
                c.execute('INSERT INTO industry_groups VALUES(?,?,?,?)',
                          (run_id, group['id'], canonical(summary), canonical(group)))
            if member_prices is not None:
                from guanlan_data.repositories.industry_index.member_prices import encoded
                c.executemany('INSERT INTO industry_member_prices VALUES(?,?,?,?)',
                    ((run_id,code,canonical(value['summary']),encoded(value)) for code,value in member_prices.items()))
        c.execute("INSERT OR REPLACE INTO industry_meta VALUES('schema',?)", (SCHEMA,))
        c.execute("INSERT OR REPLACE INTO industry_meta VALUES('current',?)", (run_id,))
        if before_commit: before_commit()
        c.commit()
        return {'run_id': run_id, 'idempotent': bool(previous), 'database': str(target),
                'groups': len(groups), 'content_hash': payload_hash}
    except BaseException:
        c.rollback(); raise
    finally: c.close()


def query(path, *, view='summary', industry=None, run_id=None):
    if view not in ('summary', 'detail', 'members'): raise ValueError('INVALID_VIEW')
    if view != 'summary' and industry not in {f'I{i:02}' for i in range(1, 31)}:
        raise ValueError('INVALID_INDUSTRY')
    with readonly(path) as c:
        schema = c.execute("SELECT value FROM industry_meta WHERE key='schema'").fetchone()
        if not schema or schema[0] != SCHEMA: raise ValueError('RESULT_SCHEMA_UNSUPPORTED')
        if run_id is None:
            current = c.execute("SELECT value FROM industry_meta WHERE key='current'").fetchone()
            if not current: raise ValueError('NO_SUCCESSFUL_RUN')
            run_id = current[0]
        run = c.execute('SELECT header_json,created_at FROM industry_runs WHERE run_id=?', (run_id,)).fetchone()
        if run is None: raise ValueError('UNKNOWN_RUN')
        result = {'schema_version': SCHEMA, 'run_id': run_id, 'header': json.loads(run[0]), 'built_at': run[1]}
        if view == 'summary':
            result['groups'] = [json.loads(r[0]) for r in c.execute(
                'SELECT summary_json FROM industry_groups WHERE run_id=? ORDER BY industry', (run_id,))]
        else:
            row = c.execute('SELECT detail_json FROM industry_groups WHERE run_id=? AND industry=?',
                            (run_id, industry)).fetchone()
            if row is None: raise ValueError('INDUSTRY_NOT_IN_RUN')
            data = json.loads(row[0])
            result['group'] = ({k: data[k] for k in ('id', 'name', 'members', 'excluded')} if view == 'members' else data)
        return result

def menu_weekly(path,revision):
    with readonly(path) as c:
        return {identifier:json.loads(points) for identifier,points in c.execute(
            "SELECT industry,json_extract(detail_json,'$.weekly_points') FROM industry_groups WHERE run_id=?",(revision,))}
