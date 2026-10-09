"""Lossless check-event normalization. All DDL runs in caller's transaction."""
import json
from guanlan_data.repositories.stock_profile.store import js; from guanlan_data.repositories.stock_profile.store import sha

EVENT_FIELDS=('checked_at','acquired_at','observed_response_id','retained_previous','effective_document_id')
def schema(c):
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_check_heads'").fetchone():
        if 'contract_version' not in {r[1] for r in c.execute('PRAGMA table_info(sp_check_results)')}:c.execute("ALTER TABLE sp_check_results ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'fundamentals-checks-2.1'")
        return
    c.execute("CREATE TABLE sp_check_results(id INTEGER PRIMARY KEY,result_hash TEXT UNIQUE NOT NULL,payload_json TEXT NOT NULL,contract_version TEXT NOT NULL DEFAULT 'fundamentals-checks-2.1')")
    c.execute('CREATE TABLE sp_check_events(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL,slot TEXT NOT NULL,checked_at TEXT NOT NULL,acquired_at TEXT,response_id INTEGER,result_id INTEGER NOT NULL,retained_previous INTEGER NOT NULL,effective_document_id INTEGER,UNIQUE(company_id,slot,checked_at))')
    c.execute('CREATE INDEX sp_check_history ON sp_check_events(company_id,slot,checked_at DESC,id DESC)')
    c.execute('CREATE TABLE sp_check_heads(company_id INTEGER,slot TEXT,event_id INTEGER NOT NULL,PRIMARY KEY(company_id,slot)) WITHOUT ROWID')
    c.execute('CREATE TABLE IF NOT EXISTS sp_import_receipts(operation_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,receipt_json TEXT NOT NULL)')
    old=c.execute("SELECT type FROM sqlite_master WHERE name='sp_component_checks'").fetchone()
    if old and old[0]=='table':
        for r in c.execute('SELECT * FROM sp_component_checks ORDER BY checked_at,id').fetchall():
            value=json.loads(r['payload_json']);put(c,r['company_id'],r['slot'],value,event_id=r['id'])
            event=c.execute('SELECT e.*,r.payload_json FROM sp_check_events e JOIN sp_check_results r ON r.id=e.result_id WHERE e.id=?',(r['id'],)).fetchone()
            if unpack(event)!=value:raise ValueError('check migration semantic mismatch')
        c.execute('DROP TABLE sp_component_checks')
    # Read-only compatibility shape; old writers fail rather than creating a second history.
    c.execute('''CREATE VIEW sp_component_checks AS SELECT e.id,e.company_id,e.slot,e.checked_at,e.response_id,
      json_set(r.payload_json,'$.checked_at',e.checked_at,'$.acquired_at',e.acquired_at,
       '$.observed_response_id',e.response_id,'$.retained_previous',json(CASE WHEN e.retained_previous THEN 'true' ELSE 'false' END),
       '$.effective_document_id',e.effective_document_id) AS payload_json
      FROM sp_check_events e JOIN sp_check_results r ON r.id=e.result_id''')
    c.execute("INSERT OR REPLACE INTO sp_meta VALUES('provider_storage_version','2.2')")

def put(c,cid,slot,value,event_id=None):
    payload={k:v for k,v in value.items() if k not in EVENT_FIELDS};h=sha(payload)
    c.execute('INSERT OR IGNORE INTO sp_check_results(result_hash,payload_json) VALUES(?,?)',(h,js(payload)))
    rid=c.execute('SELECT id FROM sp_check_results WHERE result_hash=?',(h,)).fetchone()[0]
    c.execute('INSERT OR IGNORE INTO sp_check_events VALUES(?,?,?,?,?,?,?,?,?)',(event_id,cid,slot,value['checked_at'],value.get('acquired_at'),value.get('observed_response_id'),rid,int(bool(value.get('retained_previous'))),value.get('effective_document_id')))
    row=c.execute('SELECT id FROM sp_check_events WHERE company_id=? AND slot=? AND checked_at=?',(cid,slot,value['checked_at'])).fetchone()
    c.execute('''INSERT INTO sp_check_heads VALUES(?,?,?) ON CONFLICT(company_id,slot) DO UPDATE SET event_id=excluded.event_id
      WHERE (SELECT checked_at FROM sp_check_events WHERE id=excluded.event_id)>=(SELECT checked_at FROM sp_check_events WHERE id=sp_check_heads.event_id)''',(cid,slot,row[0]))

def unpack(r):
    return {**json.loads(r['payload_json']), 'checked_at':r['checked_at'],'acquired_at':r['acquired_at'],
      'observed_response_id':r['response_id'],'retained_previous':bool(r['retained_previous']),'effective_document_id':r['effective_document_id']}

def latest(c,cid,as_of=None):
    if as_of:
        rows=c.execute('''SELECT e.*,r.payload_json FROM sp_check_heads h JOIN sp_check_events e ON e.id=(
          SELECT id FROM sp_check_events WHERE company_id=h.company_id AND slot=h.slot AND checked_at<=? ORDER BY checked_at DESC,id DESC LIMIT 1)
          JOIN sp_check_results r ON r.id=e.result_id WHERE h.company_id=?''',(as_of,cid))
    else:
        rows=c.execute('SELECT e.*,r.payload_json FROM sp_check_heads h JOIN sp_check_events e ON e.id=h.event_id JOIN sp_check_results r ON r.id=e.result_id WHERE h.company_id=?',(cid,))
    return [unpack(r) for r in rows]
