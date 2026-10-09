"""Additive provider snapshots and compact evidence. Legacy documents are unchanged."""
import json
from guanlan_data.repositories.stock_profile.store import js; from guanlan_data.repositories.stock_profile.store import sha

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS sp_api_responses(id INTEGER PRIMARY KEY,object_hash TEXT NOT NULL,path TEXT NOT NULL,interface TEXT NOT NULL,params_json TEXT NOT NULL,collected_at TEXT NOT NULL,UNIQUE(object_hash,interface,params_json,collected_at))''')
    c.execute('''CREATE TABLE IF NOT EXISTS sp_provider_checks(company_id INTEGER,slot TEXT,checked_at TEXT,acquired_at TEXT,PRIMARY KEY(company_id,slot))''')
    from guanlan_data.repositories.stock_profile.check_storage import schema as check_schema
    check_schema(c)

def content_hash(doc):
    if doc.get('extra',{}).get('snapshot_v2'):
        # Raw response identity still includes original order. Business identity
        # includes values/relationships, never positional evidence references.
        def clean(v):
            return {k:x for k,x in v.items() if k not in ('key','ref','evidence','collected_at','checked_at','item_order')}
        cxmap={x['key']:(x['label'],x['dimension'],x.get('period_end')) for x in doc.get('contexts',[])}
        fmap={x['key']:x['original_name'] for x in doc.get('facts',[])}
        value={k:v for k,v in doc.items() if k not in ('collected_at','evidence','contexts','facts','links','source_records','extra')}
        value['extra']={k:v for k,v in doc.get('extra',{}).items() if k not in ('content_hash','report_period')}
        value['contexts']=sorted([{**clean(x),'observations':[clean(o) for o in x['observations']]} for x in doc.get('contexts',[])],key=js)
        value['facts']=sorted([clean(x) for x in doc.get('facts',[])],key=js)
        value['links']=sorted([{**{k:v for k,v in x.items() if k not in ('fact_key','context_key')},'fact':fmap[x['fact_key']],'context':cxmap[x['context_key']]} for x in doc.get('links',[])],key=js)
        value['source_records']=sorted([{**clean(x),'data':clean(x['data'])} for x in doc.get('source_records',[])],key=js)
        return sha(value)
    value={k:v for k,v in doc.items() if k not in ('collected_at','evidence','extra')}
    value['extra']={k:v for k,v in doc.get('extra',{}).items() if k not in ('content_hash','report_period')}
    value['evidence']=[dict(ref=e['ref'],kind=e['kind'],row_hash=e.get('locator',{}).get('semantic_hash',e.get('locator',{}).get('row_hash')),quote=e.get('quote')) for e in doc.get('evidence',[])]
    # Acquisition timestamps belong to source records, not company fact identity.
    value['source_records']=[{**r,'data':{k:v for k,v in r['data'].items() if k not in ('collected_at','checked_at')}} for r in doc.get('source_records',[])]
    return sha(value)

def register_response(c,obj,interface,params,collected_at):
    schema(c);params=js(params)
    values=(obj['hash'],obj['path'],interface,params,collected_at)
    c.execute('INSERT OR IGNORE INTO sp_api_responses(object_hash,path,interface,params_json,collected_at) VALUES(?,?,?,?,?)',values)
    return c.execute('SELECT id FROM sp_api_responses WHERE object_hash=? AND interface=? AND params_json=? AND collected_at=?',(values[0],interface,params,collected_at)).fetchone()[0]

def record_checks(c,cid,checks,active_docs,created,documents):
    """Persist latest observations even when no valid document can be promoted."""
    schema(c);retained=[]
    for doc in list(documents):
        old=active_docs.get(doc['slot'])
        if not old:continue
        newer=c.execute('SELECT acquired_at FROM sp_provider_checks WHERE company_id=? AND slot=?',(cid,doc['slot'])).fetchone()
        last=(newer[0] if newer else None) or old.get('collected_at')
        if last and doc.get('collected_at') and doc['collected_at']<last:
            documents.remove(doc);retained.append(doc['slot'])
            for item in checks:
                if item['slot']==doc['slot']:item.update(validation_status='invalid',reason_codes=sorted(set(item['reason_codes']+['older_source_candidate'])),blocking=True)
            continue
        nulls={(x['label'],x['dimension'],o['metric']) for x in doc.get('contexts',[]) for o in x['observations'] if o['value'] is None and o['metric']=='营业收入'}
        if not nulls:continue
        actual={(r[0],r[1],r[2]) for r in c.execute('SELECT cx.label,cx.dimension,o.metric FROM sp_contexts cx JOIN sp_observations o ON o.context_id=cx.id WHERE cx.document_id=? AND o.value IS NOT NULL',(old['id'],))}
        if nulls & actual:
            documents.remove(doc);retained.append(doc['slot'])
    admitted={d['slot'] for d in documents}
    for check in checks:
        if not isinstance(check,dict) or not check.get('slot','').startswith('axdata:v2:'):raise ValueError('invalid component check')
        if check.get('validation_status') not in ('valid','partial','invalid','fetch_failed'):raise ValueError('invalid validation status')
        value=dict(check);obj=value.pop('observed_response',None)
        rid=register_response(c,obj,value['interface'],value['params'],value['acquired_at']) if obj else None
        old=active_docs.get(value['slot'])
        value.update(observed_response_id=rid,checked_at=created,retained_previous=bool(old and value['slot'] not in admitted),effective_document_id=old['id'] if old and value['slot'] not in admitted else None)
        from guanlan_data.repositories.stock_profile.check_storage import put
        put(c,cid,value['slot'],value)
    return retained

def component_checks(c,cid,docs,as_of=None):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_component_checks'").fetchone():return []
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_check_heads'").fetchone():
        from guanlan_data.repositories.stock_profile.check_storage import latest
        values=latest(c,cid,as_of);active={d['slot']:d for d in docs}
        for v in values:
            if v['slot'] in active:v['effective_document_id']=active[v['slot']]['id']
        return values
    rows=c.execute('SELECT * FROM sp_component_checks WHERE company_id=?'+(' AND checked_at<=?' if as_of else '')+' ORDER BY checked_at DESC,id DESC',[cid]+([as_of] if as_of else []))
    found={};active={d['slot']:d for d in docs}
    for r in rows:
        if r['slot'] in found:continue
        v=json.loads(r['payload_json']);doc=active.get(r['slot'])
        if doc:v['effective_document_id']=doc['id']
        found[r['slot']]=v
    return list(found.values())

def compact_locator(c,loc):
    if not loc.get('object'):return loc
    schema(c);obj=loc['object'];params=js(loc['params'])
    values=(obj['hash'],obj['path'],loc['interface'],params,loc['collected_at'])
    c.execute('INSERT OR IGNORE INTO sp_api_responses(object_hash,path,interface,params_json,collected_at) VALUES(?,?,?,?,?)',values)
    rid=c.execute('SELECT id FROM sp_api_responses WHERE object_hash=? AND interface=? AND params_json=? AND collected_at=?',(values[0],values[2],values[3],values[4])).fetchone()[0]
    return dict(response_id=rid,row_index=loc['row_index'],row_hash=loc['row_hash'])

def resolve(c,locator):
    from guanlan_data.repositories.axdata_pipeline.objects import Objects; from guanlan_data.repositories.axdata_pipeline.objects import digest
    loc=dict(locator)
    if loc.get('response_id'):
        r=c.execute('SELECT * FROM sp_api_responses WHERE id=?',(loc['response_id'],)).fetchone()
        if not r:raise ValueError('source response missing')
        loc.update(object=dict(hash=r['object_hash'],path=r['path']),interface=r['interface'],params=json.loads(r['params_json']),collected_at=r['collected_at'])
    rows=Objects.get(loc['object']);row=rows[loc['row_index']]
    if digest(row)!=loc['row_hash']:raise ValueError('source row hash mismatch')
    return row,loc
