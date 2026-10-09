"""Series tables in the existing derived database; only explicit builders write."""
from guanlan_data import sqlite as database
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from guanlan_domain.observer_series import SCHEMA
from guanlan_domain.observer_series.calculation import project


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@contextmanager
def read(path):
    with closing(database.connect(Path(path).absolute().as_uri()+'?mode=ro', uri=True, timeout=10)) as c:
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        yield c


def initialize(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with closing(database.connect(path, timeout=15)) as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and 'publications' not in tables and 'series_meta' not in tables:
            raise ValueError('不是观察器派生库')
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS series_meta(asset TEXT PRIMARY KEY,kind TEXT NOT NULL,code TEXT NOT NULL,
          start_date TEXT,end_date TEXT,input_hash TEXT NOT NULL,calendar_hash TEXT NOT NULL,method TEXT NOT NULL,
          anchor REAL,summary_json TEXT NOT NULL,updated_at TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS series_weekly(asset TEXT NOT NULL,week_start TEXT NOT NULL,trade_date TEXT,
          period_start TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,
          open_factor REAL,close_factor REAL,mixed_factor INTEGER NOT NULL,planned_last_session TEXT,
          complete INTEGER NOT NULL,quality_json TEXT NOT NULL,previous_day TEXT,
          PRIMARY KEY(asset,week_start)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS series_weekly_features(asset TEXT NOT NULL,week_start TEXT NOT NULL,
          raw_prefix TEXT NOT NULL,adjusted_prefix TEXT NOT NULL,PRIMARY KEY(asset,week_start)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS series_latest_signal(asset TEXT NOT NULL,policy TEXT NOT NULL,
          signal_json TEXT NOT NULL,PRIMARY KEY(asset,policy)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS series_bindings(ref_id TEXT PRIMARY KEY,asset TEXT NOT NULL,as_of TEXT NOT NULL,
          value_hash TEXT NOT NULL,input_hash TEXT NOT NULL,signal_json TEXT NOT NULL,ref_json TEXT NOT NULL) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS series_binding_read ON series_bindings(ref_id,asset,as_of,value_hash,input_hash,signal_json);
        CREATE TABLE IF NOT EXISTS series_group_signals(publication_id TEXT PRIMARY KEY,calendar_hash TEXT NOT NULL,
          signal_json TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS series_checkpoints(key TEXT PRIMARY KEY,value_json TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS series_failures(asset TEXT PRIMARY KEY,reason TEXT NOT NULL,job_id TEXT NOT NULL) WITHOUT ROWID;
        ''')
        row=c.execute("SELECT value_json FROM series_checkpoints WHERE key='schema'").fetchone()
        if row and json.loads(row[0])!=SCHEMA:raise ValueError('周线派生库版本不兼容')
        with c:c.execute("INSERT OR IGNORE INTO series_checkpoints VALUES('schema',?)",(canonical(SCHEMA),))


def checkpoint(c,key,default=None):
    r=c.execute('SELECT value_json FROM series_checkpoints WHERE key=?',(key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_checkpoint(c,key,value):
    c.execute('INSERT OR REPLACE INTO series_checkpoints VALUES(?,?)',(key,canonical(value)))


def decode_week(row):
    if row is None:return None
    value=dict(row)
    value['quality']=json.loads(value.pop('quality_json'))
    value['prefix']={'raw':json.loads(value.pop('raw_prefix')),'adjusted':json.loads(value.pop('adjusted_prefix'))}
    value['signals']={}
    for policy in ('raw','adjusted'):
        value['signals'][policy]=project(value,value['trade_date'],value['close'],value['close_factor'],policy=policy)
    return value


JOIN='SELECT w.*,f.raw_prefix,f.adjusted_prefix FROM series_weekly w JOIN series_weekly_features f USING(asset,week_start) '


def week(c,asset,start):
    return decode_week(c.execute(JOIN+'WHERE asset=? AND week_start=?',(asset,start)).fetchone())


def weeks(c,asset,start,end):
    return [decode_week(r) for r in c.execute(JOIN+'WHERE asset=? AND week_start BETWEEN ? AND ? ORDER BY week_start',(asset,start,end))]


def save_asset(c,asset,kind,code,rows,weekly,input_hash,calendar_hash,summary,*,from_week=None,before_commit=None,start_date=None,source_fence=None):
    # The changed suffix, the latest summary and recovery checkpoint commit together.
    from guanlan_domain.observer_series import METHOD
    if not rows:return
    c.execute('BEGIN IMMEDIATE')
    try:
        if from_week is None:
            c.execute('DELETE FROM series_weekly WHERE asset=?',(asset,))
            c.execute('DELETE FROM series_weekly_features WHERE asset=?',(asset,))
        else:
            c.execute('DELETE FROM series_weekly WHERE asset=? AND week_start>=?',(asset,from_week))
            c.execute('DELETE FROM series_weekly_features WHERE asset=? AND week_start>=?',(asset,from_week))
        selected=[w for w in weekly if from_week is None or w['week_start']>=from_week]
        fields=('week_start','trade_date','period_start','open','high','low','close','volume','amount','open_factor',
                'close_factor','mixed_factor','planned_last_session','complete')
        c.executemany('INSERT INTO series_weekly VALUES('+','.join('?' for _ in range(17))+')',
                      ((asset,*(w[k] for k in fields),canonical(w['quality']),w['previous_day']) for w in selected))
        c.executemany('INSERT INTO series_weekly_features VALUES(?,?,?,?)',
                      ((asset,w['week_start'],canonical(w['prefix']['raw']),canonical(w['prefix']['adjusted'])) for w in selected))
        anchor=weekly[-1]['close_factor']
        c.execute('INSERT OR REPLACE INTO series_meta VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (asset,kind,code,start_date or rows[0]['trade_date'],rows[-1]['trade_date'],input_hash,calendar_hash,METHOD,anchor,
                   canonical(summary),datetime.now(timezone.utc).isoformat()))
        for policy in ('raw','adjusted'):
            signal=project(weekly[-1],rows[-1]['trade_date'],rows[-1]['close'],anchor,policy=policy)
            signal.update(summary)
            c.execute('INSERT OR REPLACE INTO series_latest_signal VALUES(?,?,?)',(asset,policy,canonical(signal)))
        c.execute('DELETE FROM series_failures WHERE asset=?',(asset,))
        set_checkpoint(c,'asset:'+asset,{'input_hash':input_hash,'date':rows[-1]['trade_date'],'source_fence':source_fence})
        if before_commit:before_commit()
        c.commit()
    except BaseException:
        c.rollback();raise
