from guanlan_data import sqlite as database
import contextlib,datetime,hashlib,json,re,sqlite3
from decimal import Decimal,InvalidOperation
from pathlib import Path
from guanlan_data.repositories.stock_profile import VERSION

def js(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False)
def sha(v):return hashlib.sha256(v if isinstance(v,bytes) else js(v).encode()).hexdigest()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='microseconds')
def stamp(v):
 if v is None:return None
 d=datetime.datetime.fromisoformat(v)
 if d.tzinfo is None:d=d.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
 return d.astimezone(datetime.timezone.utc).isoformat(timespec='microseconds')
def period(v):
 if v is None:return None
 if re.fullmatch(r'\d{8}',v):v=f'{v[:4]}-{v[4:6]}-{v[6:]}'
 return datetime.date.fromisoformat(v).isoformat()
def dec(v):
 if v is None:return None
 if isinstance(v,(float,bool)):raise ValueError('decimal amounts must be strings or integers, not float/bool')
 try:d=Decimal(str(v).replace(',','').replace('%',''))
 except InvalidOperation:raise ValueError('invalid decimal')
 if not d.is_finite():raise ValueError('non-finite decimal')
 return format(d,'f')
def items(v):return v if isinstance(v,list) else [v] if v else []
def check_fields(v,allowed,required=()):
 if not isinstance(v,dict) or set(v)-set(allowed) or set(required)-set(v):raise ValueError('invalid fields; allowed='+','.join(allowed))
def status(v,trusted=False):
 if v not in ('model_initial','source_supported','needs_review','verified'):raise ValueError('invalid result status')
 if v=='verified' and not trusted:raise ValueError('public imports cannot claim verified')
 return v
def extra(v):
 if not isinstance(v,dict) or len(js(v).encode('utf-8'))>4096:raise ValueError('extra must be an object of at most 4096 UTF-8 bytes')
 return js(v)
@contextlib.contextmanager
def connect(db,write=False):
 p=Path(db).resolve()
 if write:p.parent.mkdir(parents=True,exist_ok=True)
 c=database.connect(str(p) if write else p.as_uri()+'?mode=ro',uri=not write,timeout=30,isolation_level=None)
 c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=30000')
 if not write:c.execute('PRAGMA query_only=ON')
 try:
  c.execute('BEGIN IMMEDIATE' if write else 'BEGIN');yield c
  c.commit()
 except: c.rollback();raise
 finally:c.close()
def initialize(db,rules):
 p=Path(db);p.parent.mkdir(parents=True,exist_ok=True)
 c=database.connect(p);c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON')
 c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
 for table in ('sp_documents','sp_facts'):
  if 'extra_json' not in {r[1] for r in c.execute('PRAGMA table_info('+table+')')}:c.execute("ALTER TABLE "+table+" ADD COLUMN extra_json TEXT NOT NULL DEFAULT '{}'")
 c.execute('INSERT OR IGNORE INTO sp_meta VALUES(?,?)',('schema_version',VERSION))
 if c.execute('SELECT value FROM sp_meta WHERE key=?',('schema_version',)).fetchone()[0]!=VERSION:raise ValueError('unsupported schema version')
 for r in rules:
  row=(r['scheme'],r['version'],r['industry'],r['name'],r['inclusion'],r['exclusion'])
  existing=c.execute('SELECT * FROM sp_rules WHERE scheme=? AND version=? AND industry=?',row[:3]).fetchone()
  if existing is not None and tuple(existing)!=row:c.close();raise ValueError('rule content changed without a new version')
  c.execute('INSERT OR IGNORE INTO sp_rules VALUES(?,?,?,?,?,?)',row)
 c.commit();c.close()
def insert(c,table,v):
 keys=list(v);return c.execute('INSERT INTO '+table+'('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',[v[k] for k in keys]).lastrowid
def artifact(c,a):
 if not a:return None
 check_fields(a,('sha256','kind','byte_size','path','availability'),('sha256','kind'))
 if not re.fullmatch('[a-f0-9]{64}',a['sha256']):raise ValueError('invalid artifact hash')
 r=c.execute('SELECT id FROM sp_artifacts WHERE sha256=?',(a['sha256'],)).fetchone()
 if r:return r[0]
 return insert(c,'sp_artifacts',{'sha256':a['sha256'],'kind':a['kind'],'byte_size':a.get('byte_size'),'path':a.get('path'),'availability':a.get('availability','unknown')})
def register_security(c,s):
 check_fields(s,('code','name','company_name','company_id','list_date','delist_date','status','previous_code'),('code','name'))
 if not re.fullmatch(r'\d{6}\.(SZ|SH|BJ)',s['code']):raise ValueError('exchange suffix required')
 old=c.execute('SELECT * FROM sp_securities WHERE code=?',(s.get('previous_code',s['code']),)).fetchone()
 if old:
  if s.get('company_id') not in (None,old['company_id']):raise ValueError('company mismatch')
  c.execute('INSERT OR IGNORE INTO sp_aliases(security_id,code,name,recorded_at) VALUES(?,?,?,?)',(old['id'],old['code'],old['name'],now()))
  c.execute('UPDATE sp_securities SET code=?,exchange=?,name=?,list_date=COALESCE(?,list_date),delist_date=COALESCE(?,delist_date),status=? WHERE id=?',(s['code'],s['code'][-2:],s['name'],period(s.get('list_date')),period(s.get('delist_date')),s.get('status',old['status']),old['id']))
  c.execute('UPDATE sp_companies SET name=? WHERE id=?',(s.get('company_name',s['name']),old['company_id']));return old['company_id']
 cid=s.get('company_id')
 if cid is None:cid=insert(c,'sp_companies',{'name':s.get('company_name',s['name'])})
 if not c.execute('SELECT 1 FROM sp_companies WHERE id=?',(cid,)).fetchone():raise ValueError('unknown company')
 insert(c,'sp_securities',{'company_id':cid,'code':s['code'],'exchange':s['code'][-2:],'name':s['name'],'list_date':period(s.get('list_date')),'delist_date':period(s.get('delist_date')),'status':s.get('status','unknown')});return cid
