"""Provider associations are retained independently of business classifications."""
from guanlan_data.repositories.stock_profile.store import check_fields; from guanlan_data.repositories.stock_profile.store import js; from guanlan_data.repositories.stock_profile.store import status; from guanlan_data.repositories.stock_profile.store import insert
import json
SCHEMA='''CREATE TABLE IF NOT EXISTS sp_source_records(
id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES sp_documents(id),
source_key TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,data_json TEXT NOT NULL,
evidence_id INTEGER NOT NULL REFERENCES sp_evidence(id),status TEXT NOT NULL,
UNIQUE(document_id,source_key))'''
def initialize(c):
 c.execute(SCHEMA)
 cols={r[1] for r in c.execute('PRAGMA table_info(sp_source_records)')}
 for name,kind in [('label_id','INTEGER'),('reason_text','TEXT'),('reason_present','INTEGER NOT NULL DEFAULT 0')]:
  if name not in cols:c.execute('ALTER TABLE sp_source_records ADD COLUMN '+name+' '+kind)
 c.execute('CREATE TABLE IF NOT EXISTS sp_source_labels(id INTEGER PRIMARY KEY,provider TEXT,namespace TEXT,source_id TEXT,name TEXT,UNIQUE(provider,namespace,source_id))')
 c.execute('CREATE INDEX IF NOT EXISTS sp_source_label_name ON sp_source_labels(namespace,name)')
 c.execute('CREATE INDEX IF NOT EXISTS sp_source_membership_label ON sp_source_records(label_id,document_id)')
 c.execute('CREATE INDEX IF NOT EXISTS sp_source_membership_document ON sp_source_records(document_id,label_id)')
 c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS sp_source_reason_fts USING fts5(reason_text,content='sp_source_records',content_rowid='id',tokenize='trigram')")
 c.execute("CREATE TRIGGER IF NOT EXISTS sp_source_reason_insert AFTER INSERT ON sp_source_records BEGIN INSERT INTO sp_source_reason_fts(rowid,reason_text) VALUES(new.id,new.reason_text); END")
 c.execute("CREATE TRIGGER IF NOT EXISTS sp_source_reason_update AFTER UPDATE ON sp_source_records BEGIN INSERT INTO sp_source_reason_fts(sp_source_reason_fts,rowid,reason_text) VALUES('delete',old.id,old.reason_text); INSERT INTO sp_source_reason_fts(rowid,reason_text) VALUES(new.id,new.reason_text); END")
 if 'label_id' not in cols:
  for r in c.execute('SELECT * FROM sp_source_records').fetchall():
   data=json.loads(r['data_json']);lid=label(c,r['kind'],r['name'],data);present='reason' in data;reason=data.pop('reason',None)
   c.execute('UPDATE sp_source_records SET label_id=?,reason_text=?,reason_present=?,data_json=? WHERE id=?',(lid,reason,int(present),js(data),r['id']))

def label(c,kind,name,data):
 if kind not in ('topic','industry'):return None
 namespace=data.get('namespace') or data.get('topic_type') or ('source_industry' if kind=='industry' else 'unspecified')
 provider=data.get('source_provider','TDX');sid=str(data.get('topic_id') or data.get('industry_id') or name)
 c.execute('INSERT OR IGNORE INTO sp_source_labels(provider,namespace,source_id,name) VALUES(?,?,?,?)',(provider,namespace,sid,name))
 return c.execute('SELECT id FROM sp_source_labels WHERE provider=? AND namespace=? AND source_id=?',(provider,namespace,sid)).fetchone()[0]
def ingest(c,did,rows,evidence,trusted=False):
 if not rows:return
 initialize(c)
 c.execute('CREATE INDEX IF NOT EXISTS sp_source_records_topic ON sp_source_records(kind,name,document_id)')
 for r in rows:
  check_fields(r,('key','kind','name','data','ref','status'),('key','kind','name','data','ref'))
  if r['kind'] not in ('topic','industry','event','disclosure','board') or r['ref'] not in evidence:raise ValueError('invalid source record kind/evidence')
  if not isinstance(r['data'],dict) or len(js(r['data']).encode())>131072:raise ValueError('source record too large or not object')
  data=dict(r['data']);lid=label(c,r['kind'],r['name'],data);present='reason' in data;reason=data.pop('reason',None)
  insert(c,'sp_source_records',dict(document_id=did,source_key=r['key'],kind=r['kind'],name=r['name'],data_json=js(data),reason_text=reason,reason_present=int(present),label_id=lid,evidence_id=evidence[r['ref']],status=status(r.get('status','source_supported'),trusted)))
def query(c,ids):
 if not ids or not c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_source_records'").fetchone():return []
 import json
 rows=[]
 for r in c.execute('SELECT * FROM sp_source_records WHERE document_id IN ('+','.join('?' for _ in ids)+') ORDER BY id',ids):
  x=dict(r);x['data']=json.loads(x.pop('data_json'));x['evidence_ref']='sp:'+str(x.pop('evidence_id'))
  reason=x.pop('reason_text',None)
  if x.pop('reason_present',0):x['data']['reason']=reason
  rows.append(x)
 return rows

def search_clause(c,tags,match,reason):
 if match not in ('all','any'):raise ValueError('tag_match must be all or any')
 if not isinstance(tags,list) or len(tags)>30:raise ValueError('tags must be an array of at most 30 entries')
 if not c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_source_labels'").fetchone():return '0',[]
 conditions=[];args=[]
 for tag in tags:
  t={'name':tag} if isinstance(tag,str) else tag
  if not isinstance(t,dict) or not t.get('name') or set(t)-{'name','namespace','provider'}:raise ValueError('invalid source tag filter')
  term=[]
  for key,value in t.items():term.append(('sr.' if key=='name' else 'l.')+key+'=?');args.append(value)
  conditions.append('EXISTS(SELECT 1 FROM sp_revision_documents rd JOIN sp_documents d ON d.id=rd.document_id JOIN sp_source_records sr ON sr.document_id=rd.document_id JOIN sp_source_labels l ON l.id=sr.label_id WHERE rd.revision_id=h.revision_id AND (d.period_end IS NULL OR d.period_end=h.period_end) AND '+' AND '.join(term)+')')
 clause=(' AND ' if match=='all' else ' OR ').join(conditions)
 if reason:
  if not isinstance(reason,str) or len(reason)>200:raise ValueError('reason query too long')
  if len(reason)>=3:
   term='sr.id IN (SELECT rowid FROM sp_source_reason_fts WHERE sp_source_reason_fts MATCH ?)';arg='"'+reason.replace('"','""')+'"'
  else:term='instr(COALESCE(sr.reason_text,\'\'),?)>0';arg=reason
  clause=('('+clause+') AND ' if clause else '')+'EXISTS(SELECT 1 FROM sp_revision_documents rd JOIN sp_documents d ON d.id=rd.document_id JOIN sp_source_records sr ON sr.document_id=rd.document_id WHERE rd.revision_id=h.revision_id AND (d.period_end IS NULL OR d.period_end=h.period_end) AND '+term+')';args.append(arg)
 return clause or '1',args
