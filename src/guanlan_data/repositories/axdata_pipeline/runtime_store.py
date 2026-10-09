"""Shared operational ledger. No business facts or model dependencies live here."""
from guanlan_data import sqlite as database
import contextlib, json, os, sqlite3, time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
RUNTIME = PROJECT / 'work/fundamentals/runtime.sqlite'
_READY=set()
REQUEST_LIMIT=360
MIN_INTERVAL=0.17
MAX_COMPANIES=20
DEFAULT_CONCURRENCY=12

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sends(id INTEGER PRIMARY KEY, at REAL NOT NULL, host TEXT, batch TEXT, company TEXT, request_key TEXT);
CREATE INDEX IF NOT EXISTS sends_time ON sends(at);
CREATE INDEX IF NOT EXISTS sends_batch ON sends(batch,company,request_key);
CREATE TABLE IF NOT EXISTS request_totals(batch TEXT PRIMARY KEY, sent INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS hosts(host TEXT PRIMARY KEY, until_at REAL DEFAULT 0, consecutive429 INTEGER DEFAULT 0, paused INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS clock(id INTEGER PRIMARY KEY CHECK(id=1), last_wall REAL, last_mono REAL, process INTEGER, guard_until REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS controls(batch TEXT PRIMARY KEY, paused INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS leases(key TEXT PRIMARY KEY, pid INTEGER, expires REAL);
CREATE TABLE IF NOT EXISTS runs(batch TEXT PRIMARY KEY, database_path TEXT, status TEXT, updated REAL, summary TEXT);
CREATE TABLE IF NOT EXISTS jobs(batch TEXT, company TEXT, status TEXT, updated REAL, receipt TEXT, PRIMARY KEY(batch,company));
CREATE TABLE IF NOT EXISTS company_slots(slot INTEGER PRIMARY KEY,pid INTEGER,owner TEXT);
'''

def alive(pid):
    if not pid: return False
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h: return False
        code = ctypes.c_ulong()
        try: return bool(k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))) and code.value == 259
        finally: k.CloseHandle(ctypes.c_void_p(h))
    try: os.kill(int(pid), 0); return True
    except OSError: return False

def process_identity(pid):
    """PID plus OS process creation time prevents ownership by a reused PID."""
    if os.name=='nt':
        import ctypes
        k=ctypes.windll.kernel32;k.OpenProcess.restype=ctypes.c_void_p
        h=k.OpenProcess(0x1000,False,int(pid))
        if not h:return None
        times=[ctypes.c_ulonglong() for _ in range(4)]
        try:
            if not k.GetProcessTimes(ctypes.c_void_p(h),*[ctypes.byref(t) for t in times]):return None
            return str(pid)+':'+str(times[0].value)
        finally:k.CloseHandle(ctypes.c_void_p(h))
    try:return str(pid)+':'+Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
    except OSError:return None

class Paused(Exception): pass

class Ledger:
    def __init__(self, path=RUNTIME):
        self.path = Path(path).resolve()
        if str(self.path) in _READY and self.path.exists():return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        marker=self.path.with_suffix('.initialized');lost=marker.exists() and not self.path.exists()
        c=database.connect(self.path, timeout=30)
        try:
            had_totals=c.execute("SELECT 1 FROM sqlite_master WHERE name='request_totals'").fetchone()
            c.execute('PRAGMA journal_mode=WAL');c.executescript(SCHEMA)
            c.execute('CREATE TABLE IF NOT EXISTS runtime_maintenance(key TEXT PRIMARY KEY,at REAL)')
            if 'owner' not in {r[1] for r in c.execute('PRAGMA table_info(leases)')}:c.execute('ALTER TABLE leases ADD COLUMN owner TEXT')
            if lost:c.execute('INSERT OR REPLACE INTO clock VALUES(1,?,?,?,?)',(time.time(),time.monotonic(),os.getpid(),time.time()+60))
            if not had_totals:c.execute("INSERT OR IGNORE INTO request_totals SELECT COALESCE(batch,'__legacy__'),COUNT(*) FROM sends GROUP BY batch")
            c.commit()
        finally:c.close()
        if not marker.exists():
            try:
                with marker.open('x',encoding='ascii') as f:f.write('collector-runtime-v2\n')
            except FileExistsError:pass
        _READY.add(str(self.path))
    @contextlib.contextmanager
    def connection(self,readonly=False):
        c = database.connect(self.path.as_uri()+'?mode=ro' if readonly else self.path, uri=readonly,timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            if readonly:c.execute('PRAGMA query_only=ON')
            c.execute('BEGIN' if readonly else 'BEGIN IMMEDIATE'); yield c; c.commit()
        except BaseException: c.rollback(); raise
        finally: c.close()
    def permit(self, host, context, at=None, mono=None):
        """Reserve at the urllib send audit event; callers retry after returned delay."""
        with self.connection() as c:
            wall = time.time() if at is None else at; tick = time.monotonic() if mono is None else mono
            row = c.execute('SELECT * FROM controls WHERE batch=?',(context.get('batch',''),)).fetchone()
            if row and row['paused']: raise Paused('batch paused')
            old = c.execute('SELECT * FROM clock WHERE id=1').fetchone(); guard = old['guard_until'] if old else 0
            # Same-boot monotonic clocks are shared by Windows processes. A reboot or
            # a wall clock jump gets a full quiet window, not an early permit.
            if old and (tick < old['last_mono'] or abs((wall-old['last_wall'])-(tick-old['last_mono'])) > 2):
                guard = wall + 60
            c.execute('INSERT OR REPLACE INTO clock VALUES(1,?,?,?,?)',(wall,tick,os.getpid(),guard))
            state = c.execute('SELECT * FROM hosts WHERE host=?',(host,)).fetchone()
            if state and state['paused']: raise Paused('source paused: '+host)
            recent = [r[0] for r in c.execute('SELECT at FROM sends WHERE at>? ORDER BY at',(wall-60,))]
            wait = max(0,guard-wall,(state['until_at']-wall) if state else 0)
            if recent: wait = max(wait, recent[-1]+MIN_INTERVAL-wall)
            if len(recent)>=REQUEST_LIMIT: wait=max(wait,recent[-REQUEST_LIMIT]+60.001-wall)
            if wait>0: return wait
            c.execute('INSERT INTO sends(at,host,batch,company,request_key) VALUES(?,?,?,?,?)',(wall,host,context.get('batch'),context.get('company'),context.get('key')))
            c.execute('INSERT INTO request_totals VALUES(?,1) ON CONFLICT(batch) DO UPDATE SET sent=sent+1',(context.get('batch') or '__legacy__',))
            return 0
    def success(self,host):
        with self.connection() as c: c.execute('UPDATE hosts SET consecutive429=0 WHERE host=?',(host,))
    def cooldown(self,host,seconds,limited=False):
        with self.connection() as c:
            c.execute('INSERT OR IGNORE INTO hosts(host) VALUES(?)',(host,))
            c.execute('UPDATE hosts SET until_at=MAX(until_at,?),consecutive429=CASE WHEN ? THEN consecutive429+1 ELSE consecutive429 END WHERE host=?',(time.time()+seconds,int(limited),host))
            c.execute('UPDATE hosts SET paused=1 WHERE host=? AND consecutive429>=3',(host,))
    def control(self,batch,paused):
        with self.connection() as c: c.execute('INSERT OR REPLACE INTO controls VALUES(?,?)',(str(batch),int(paused)))
    def resume_sources(self,hosts=None):
        with self.connection() as c:
            if hosts is None:c.execute('UPDATE hosts SET paused=0,consecutive429=0')
            else:
                if not isinstance(hosts,list) or any(not isinstance(h,str) for h in hosts):raise ValueError('sources must be host list')
                for host in hosts:c.execute('UPDATE hosts SET paused=0,consecutive429=0 WHERE host=?',(host,))
    def snapshot(self,batch=None):
        with self.connection(readonly=True) as c:
            total=c.execute('SELECT COALESCE(SUM(sent),0) FROM request_totals'+(' WHERE batch=?' if batch else ''),(str(batch),) if batch else ()).fetchone()[0]
            recent=c.execute('SELECT COUNT(*) FROM sends WHERE at>?',(time.time()-60,)).fetchone()[0]
            return dict(source_http_requests=total,last60_requests=recent,limit=REQUEST_LIMIT,min_interval=MIN_INTERVAL,window_seconds=60,hosts=[dict(r) for r in c.execute('SELECT * FROM hosts')])
    def record(self,batch,job=None,manifest=None):
        with self.connection() as c:
            if job:
                c.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)',(str(batch),job['security']['code'],job['status'],time.time(),json.dumps(dict(error=job.get('error'),import_receipt=job.get('import')),ensure_ascii=False)))
            if manifest:
                c.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)',(str(batch),manifest['database'],manifest['status'],time.time(),json.dumps(manifest.get('usage',{}))))
            last=c.execute("SELECT at FROM runtime_maintenance WHERE key='retention'").fetchone()
            if not last or time.time()-last[0]>3600:
                c.execute('DELETE FROM sends WHERE at<?',(time.time()-90*86400,))
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='collection_events'").fetchone():c.execute('DELETE FROM collection_events WHERE at<?',(time.time()-90*86400,))
                c.execute("INSERT OR REPLACE INTO runtime_maintenance VALUES('retention',?)",(time.time(),))
    @contextlib.contextmanager
    def company_slot(self,batch):
        owner=process_identity(os.getpid());chosen=None
        if not owner:raise RuntimeError('cannot establish company owner')
        while chosen is None:
            with self.connection() as c:
                paused=c.execute('SELECT paused FROM controls WHERE batch=?',(str(batch),)).fetchone()
                if paused and paused[0]:raise Paused('batch paused')
                rows={r['slot']:r for r in c.execute('SELECT * FROM company_slots')}
                for slot in range(MAX_COMPANIES):
                    row=rows.get(slot)
                    if not row or not alive(row['pid']) or process_identity(row['pid']) not in (None,row['owner']):
                        chosen=slot;c.execute('INSERT OR REPLACE INTO company_slots VALUES(?,?,?)',(slot,os.getpid(),owner));break
            if chosen is None:time.sleep(.25)
        try:yield
        finally:
            with self.connection() as c:c.execute('DELETE FROM company_slots WHERE slot=? AND pid=? AND owner=?',(chosen,os.getpid(),owner))

    @contextlib.contextmanager
    def lease(self,key,event=lambda **kw:None):
        owner=process_identity(os.getpid())
        if owner is None:raise RuntimeError('cannot establish worker process identity')
        while True:
            acquired=False
            with self.connection() as c:
                row=c.execute('SELECT * FROM leases WHERE key=?',(key,)).fetchone()
                identity=process_identity(row['pid']) if row else None
                if not row or not alive(row['pid']) or row['owner'] and identity is not None and row['owner']!=identity:
                    c.execute('INSERT OR REPLACE INTO leases(key,pid,expires,owner) VALUES(?,?,?,?)',(key,os.getpid(),time.time()+86400,owner)); acquired=True
            if acquired: break
            event(event='waiting_shared_request'); time.sleep(.25)
        try: yield
        finally:
            with self.connection() as c: c.execute('DELETE FROM leases WHERE key=? AND pid=? AND owner=?',(key,os.getpid(),owner))
