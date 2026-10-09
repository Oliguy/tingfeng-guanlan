"""Resolve only registered company documents, never caller-provided source text."""
import hashlib
import json
import re
from pathlib import Path

from guanlan_data.repositories.stock_profile.archives import resolve
from guanlan_data.repositories.stock_profile.query import find_security
from guanlan_data.repositories.stock_profile.store import sha


def current_documents(c, cid, report_period):
    return {r['id']:dict(r) for r in c.execute('''SELECT d.* FROM sp_heads h
        JOIN sp_revision_documents rd ON rd.revision_id=h.revision_id
        JOIN sp_documents d ON d.id=rd.document_id
        WHERE h.company_id=? AND (d.period_end=? OR d.period_end IS NULL)''',(cid,report_period))}


def artifacts(c, doc):
    def get(aid):
        r=c.execute('SELECT * FROM sp_artifacts WHERE id=?',(aid,)).fetchone()
        return dict(r) if r else None
    return resolve(c,doc.get('legacy_id'),get(doc['original_artifact']),get(doc['parsed_artifact']))


def read_md(c, doc, cache):
    _, parsed=artifacts(c,doc)
    if not parsed or not parsed.get('path'):
        raise ValueError('该归档缺少登记的MD文件')
    key=(parsed['path'],parsed['sha256'])
    if key not in cache:
        b=Path(parsed['path']).read_bytes()
        if hashlib.sha256(b).hexdigest()!=parsed['sha256']:
            raise ValueError('归档MD哈希不符')
        cache[key]=b.decode('utf-8-sig').splitlines()
    return parsed,cache[key]


def read_ref(c, cid, report_period, ref, cache=None, *, documents=None):
    cache={} if cache is None else cache
    if not isinstance(ref,dict) or set(ref)-{'evidence_ref','fact_id','document_id','line_start','line_end'}:
        raise ValueError('证据只接受登记证据ID或文档ID与行号')
    docs=current_documents(c,cid,report_period) if documents is None else documents
    fact=None
    if ref.get('fact_id') is not None:
        fact=c.execute('SELECT * FROM sp_facts WHERE id=?',(ref['fact_id'],)).fetchone()
        if not fact or fact['document_id'] not in docs:
            raise ValueError('业务事实不属于该公司当前期间资料')
    eid=ref.get('evidence_ref')
    if eid:
        if any(k in ref for k in ('document_id','line_start','line_end')) or not re.fullmatch(r'sp:\d+',eid):
            raise ValueError('不能混用证据ID和行号定位')
        row=c.execute('SELECT * FROM sp_evidence WHERE id=?',(int(eid[3:]),)).fetchone()
        if not row or row['document_id'] not in docs:
            raise ValueError('证据不属于该公司当前期间资料')
        if fact and not c.execute('SELECT 1 FROM sp_fact_evidence WHERE fact_id=? AND evidence_id=?',(fact['id'],row['id'])).fetchone():
            raise ValueError('证据未关联指定业务事实')
        doc=docs[row['document_id']]
        quote=row['quote']
        if quote is None:
            locator=json.loads(row['locator_json'])
            if locator.get('response_id') or locator.get('object'):
                from guanlan_data.repositories.stock_profile.provider_storage import resolve as resolve_provider
                structured,_=resolve_provider(c,locator)
                quote=json.dumps(structured,ensure_ascii=False)
            elif row['line_start']:
                _,lines=read_md(c,doc,cache)
                quote='\n'.join(lines[row['line_start']-1:row['line_end']])
            else:
                raise ValueError('证据没有可读取正文')
        line_start,line_end,page=row['line_start'],row['line_end'],row['pdf_page']
        original,parsed=artifacts(c,doc)
        if parsed and parsed.get('path'):
            read_md(c,doc,cache)
        source_hash=parsed['sha256'] if parsed else original['sha256'] if original else sha(quote)
        path=parsed['path'] if parsed else original['path'] if original else None
        evidence_id=row['id']
    else:
        did=ref.get('document_id')
        if did not in docs:
            raise ValueError('文档不属于该公司当前期间资料')
        doc=docs[did]
        if fact and fact['document_id']!=did:
            raise ValueError('事实与文档不匹配')
        parsed,lines=read_md(c,doc,cache)
        line_start,line_end=ref.get('line_start'),ref.get('line_end')
        if type(line_start) is not int or type(line_end) is not int or not 1<=line_start<=line_end<=len(lines) or line_end-line_start>79:
            raise ValueError('证据行号须有效且最多80行')
        quote='\n'.join(lines[line_start-1:line_end])
        page=None
        for line in reversed(lines[:line_start]):
            m=re.match(r'<!-- mineru-page: (\d+) -->',line)
            if m:
                page=int(m.group(1));break
        source_hash,path,evidence_id=parsed['sha256'],parsed['path'],None
    if not isinstance(quote,str) or not quote.strip() or len(quote)>20000:
        raise ValueError('证据应为非空有界原文，最多20000字')
    result=dict(input_ref=ref,document_id=doc['id'],fact_id=fact['id'] if fact else None,evidence_id=evidence_id,
        title=doc['title'],report_period=doc['period_end'],quote=quote,line_start=line_start,line_end=line_end,
        pdf_page=page,path=path,sha256=source_hash,fact_stage=fact['stage'] if fact else None,
        fact_status=fact['status'] if fact else None)
    result['evidence_hash']=sha({k:result[k] for k in ('document_id','fact_id','evidence_id','quote','line_start','line_end','pdf_page','sha256','fact_stage','fact_status')})
    return result


def business_input(c, cid, report_period, cache):
    docs=current_documents(c,cid,report_period)
    facts=[]
    if docs:
        q=','.join('?' for _ in docs)
        for row in c.execute(f'SELECT * FROM sp_facts WHERE document_id IN ({q}) ORDER BY id',list(docs)):
            r=dict(row)
            refs=[{'fact_id':r['id'],'evidence_ref':'sp:'+str(x[0])} for x in c.execute('SELECT DISTINCT evidence_id FROM sp_fact_evidence WHERE fact_id=? ORDER BY evidence_id',(r['id'],))]
            facts.append(dict(id=r['id'],document_id=r['document_id'],name=r['standard_name'],description=r['description'],products=json.loads(r['products_json']),stage=r['stage'],status=r['status'],refs=refs))
    # Financial-only document revisions are intentionally excluded.
    relevant={f['document_id'] for f in facts}|{d['id'] for d in docs.values() if d['kind'] in ('annual','half_year','announcement','interaction')}
    hashes=[]
    for did in sorted(relevant):
        doc=docs[did]
        original,parsed=artifacts(c,doc)
        if parsed and parsed.get('path'):
            read_md(c,doc,cache)
        hashes.append([did,parsed['sha256'] if parsed else original['sha256'] if original else None])
    return facts,sha({'facts':facts,'sources':hashes})
