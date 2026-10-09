"""Only this independent training store is writable. No market row snapshots."""
from guanlan_data import sqlite as database
from contextlib import contextmanager
from pathlib import Path
import json,sqlite3,threading

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self._lock=threading.RLock()
        # Only the independent training state uses WAL. Keep it open so every
        # round does not create/delete journals; FULL retains durable commit.
        self._keeper=database.connect(self.path,timeout=10,check_same_thread=False)
        self._keeper.row_factory=sqlite3.Row
        self._keeper.execute('PRAGMA journal_mode=WAL')
        self._keeper.execute('PRAGMA synchronous=FULL')
        with self.transaction() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
              CREATE TABLE IF NOT EXISTS actions(session_id TEXT,revision INTEGER,request_id TEXT UNIQUE,receipt TEXT NOT NULL,PRIMARY KEY(session_id,revision));
              CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,operation TEXT NOT NULL,params_hash TEXT NOT NULL,session_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS notes(session_id TEXT,revision INTEGER,note TEXT NOT NULL,mistake INTEGER NOT NULL DEFAULT 0,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(session_id,revision));
            ''')
    def close(self):
        with self._lock:
            keeper=getattr(self,'_keeper',None)
            if keeper is not None:keeper.close();self._keeper=None
    def __del__(self):
        try:self.close()
        except Exception:pass
    @contextmanager
    def transaction(self):
        with self._lock:
            c=self._keeper
            if c is None:raise ValueError('训练状态连接已关闭，请重开服务')
            try:
                c.execute('BEGIN IMMEDIATE')
                yield c;c.commit()
            except Exception:
                c.rollback();raise
    def get(self,c,id):
        r=c.execute('SELECT payload FROM sessions WHERE id=?',(id,)).fetchone()
        if not r:raise ValueError('练习不存在，请返回训练首页')
        return json.loads(r[0])
    def save(self,c,s):
        c.execute('INSERT INTO sessions(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=CURRENT_TIMESTAMP',(s['id'],json.dumps(s,ensure_ascii=False,allow_nan=False,separators=(',',':'))))
    def actions(self,c,id):return [json.loads(r[0]) for r in c.execute('SELECT receipt FROM actions WHERE session_id=? ORDER BY revision',(id,))]
