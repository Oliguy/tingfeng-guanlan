import json
import re
import time
import uuid

from guanlan_data.repositories.stock_profile.query import find_security
from guanlan_data.repositories.stock_profile.store import connect; from guanlan_data.repositories.stock_profile.store import insert; from guanlan_data.repositories.stock_profile.store import js; from guanlan_data.repositories.stock_profile.store import now; from guanlan_data.repositories.stock_profile.store import period; from guanlan_data.repositories.stock_profile.store import sha
import guanlan_data.repositories.industry_taxonomy.catalog as catalog
from guanlan_data.repositories.industry_taxonomy.evidence import read_ref; from guanlan_data.repositories.industry_taxonomy.evidence import business_input

STAGES={'actual','development','investment','unclear'}


def _codes(c,codes):
    if not isinstance(codes,list) or not 1<=len(codes)<=200 or len(set(codes))!=len(codes):
        raise ValueError('codes须为1至200个不重复股票代码')
    return [find_security(c,code) for code in codes]


def _selected(nodes,node_ids):
    if node_ids is None:
        return {x for x in nodes if nodes[x]['parent_id'] is not None and catalog.active(nodes,x)}
    if not isinstance(node_ids,list) or not node_ids or len(set(node_ids))!=len(node_ids):
        raise ValueError('node_ids须为不重复非空数组')
    result=set()
    for x in node_ids:
        if x not in nodes or not catalog.active(nodes,x):
            raise ValueError('节点不存在或已停用: '+str(x))
        result |= catalog.descendants(nodes,x)
    return {x for x in result if nodes[x]['parent_id'] is not None and catalog.active(nodes,x)}


def _matches(fact,node):
    text=' '.join([fact['name'],*fact['products'],fact['description'] or '']).casefold()
    rule=node['rule']
    def found(term):
        # InP must not match input; English abbreviations are tokens.
        return bool(re.search(r'(?<![a-z])'+re.escape(term.lower())+r'(?![a-z])',text)) if term.isascii() else term.casefold() in text
    hits=[x for x in rule['terms'] if found(x)]
    return hits if hits and (not rule.get('context_terms') or any(found(x) for x in rule['context_terms'])) else []


def _leaf_rows(c):
    return c.execute('''SELECT m.* FROM sp_taxonomy_memberships m
        WHERE NOT EXISTS(SELECT 1 FROM sp_taxonomy_memberships n WHERE n.prior_id=m.id) ORDER BY m.id''').fetchall()


def preview(db,codes,report_period='20260630',node_ids=None,reviews=None,actor='codex',reason='规则候选预览'):
    started=time.perf_counter()
    rp=period(report_period)
    if not isinstance(actor,str) or not actor.strip() or not isinstance(reason,str) or not reason.strip():
        raise ValueError('须提供actor和reason')
    reviews=[] if reviews is None else reviews
    if not isinstance(reviews,list) or len(reviews)>2000:
        raise ValueError('reviews须为最多2000项审阅数组')
    cache={}
    with connect(db) as c:
        rev,nodes=catalog.load(c)
        selected=_selected(nodes,node_ids)
        securities=_codes(c,codes)
        facts_by_code={};inputs={};security_by_code={s['code']:s for s in securities}
        for s in securities:
            facts,fp=business_input(c,s['company_id'],rp,cache)
            facts_by_code[s['code']]=facts;inputs[s['code']]=fp
        node_hashes={x:catalog.rule_hash(nodes,x) for x in sorted(selected)}
        req_hash=sha(dict(codes=sorted(codes),period=rp,node_hashes=node_hashes,reviews=reviews,actor=actor,reason=reason))
        input_hash=sha(inputs)
        cached=c.execute("SELECT payload_json FROM sp_taxonomy_runs WHERE kind='preview' AND request_hash=? AND input_hash=? ORDER BY created_at DESC LIMIT 1",(req_hash,input_hash)).fetchone()
        reused=False
        if cached:
            previous=json.loads(cached[0])
            # Re-resolve every bound source. No cached candidate can hide source drift.
            for item in previous['candidates']:
                for e in item['evidence']:
                    if read_ref(c,item['company_id'],rp,e['input_ref'],cache)['evidence_hash']!=e['evidence_hash']:
                        raise ValueError('缓存证据发生变化，请重新审阅')
            candidates=previous['candidates'];gaps=previous['gaps'];reused=True
        else:
            candidates=[];gaps=[];covered=set()
            for review in reviews:
                allowed={'code','node_id','product','stage','reason','refs','role','stage_reason'}
                if not isinstance(review,dict) or set(review)!=allowed:
                    raise ValueError('审阅必须提供code/node_id/product/stage/reason/refs/role/stage_reason')
                code,nid=review['code'],review['node_id']
                if code not in security_by_code or nid not in selected or not nodes[nid]['rule']['terms']:
                    raise ValueError('审阅超出本次股票或叶子/业务节点范围')
                if review['stage'] not in STAGES or not all(isinstance(review[k],str) and review[k].strip() for k in ('product','reason','role','stage_reason')):
                    raise ValueError('审阅需具体产品、角色、经营阶段及其依据')
                if not isinstance(review['refs'],list) or not 1<=len(review['refs'])<=12:
                    raise ValueError('每条审阅须有1至12条证据')
                sec=security_by_code[code]
                evidence=[read_ref(c,sec['company_id'],rp,r,cache) for r in review['refs']]
                if review['stage']=='actual' and review['role'] in ('customer','purchaser','investor','researcher'):
                    raise ValueError('采购、客户、投资和纯研发角色不能标实际生产/经营')
                if review['stage']=='actual' and all(e['fact_stage'] in ('development','investment') for e in evidence):
                    raise ValueError('仅研发或投资事实不足以支持实际经营')
                item=dict(code=code,name=sec['name'],company_id=sec['company_id'],node_id=nid,product=review['product'],stage=review['stage'],status='source_supported',reason=review['reason'],role=review['role'],stage_reason=review['stage_reason'],method='agent_review',actor=actor,evidence=evidence,rule_hash=node_hashes[nid])
                item['candidate_id']=sha({k:item[k] for k in ('code','node_id','product')})[:24]
                if item['candidate_id'] in {x['candidate_id'] for x in candidates}:
                    raise ValueError('同一股票/节点/产品不能提交重复审阅；合并证据后提交')
                candidates.append(item);covered.add((code,nid))
            for sec in securities:
                code=sec['code'];facts=facts_by_code[code]
                if not facts:
                    gaps.append(dict(code=code,type='facts_missing',reason='尚无结构化业务事实；可补读已归档文档并提交refs',resolved_by_review=any(x['code']==code for x in candidates)))
                for nid in sorted(selected):
                    if (code,nid) in covered or not nodes[nid]['rule']['terms']:
                        continue
                    found=False
                    for fact in facts:
                        hits=_matches(fact,nodes[nid])
                        if not hits:continue
                        found=True;evidence=[];errors=[]
                        for ref in fact['refs'][:3]:
                            try:evidence.append(read_ref(c,sec['company_id'],rp,ref,cache))
                            except (ValueError,OSError) as exc:errors.append(str(exc))
                        product=fact['name']
                        candidates.append(dict(candidate_id=sha([code,nid,fact['id']])[:24],code=code,name=sec['name'],company_id=sec['company_id'],node_id=nid,product=product,stage='unclear',suggested_stage=fact['stage'],status='needs_review',reason='词项命中：'+','.join(hits)+'；须审阅主体、产品和阶段',role='unknown',stage_reason='规则只生成候选，未完成语义判断',method='rule_candidate',actor=actor,evidence=evidence,rule_hash=node_hashes[nid],issues=errors))
                    if not found:
                        gaps.append(dict(code=code,node_id=nid,type='not_matched' if facts else 'not_evaluated',reason='现有事实未命中；不等于公司不属于该类' if facts else '缺少结构化事实，尚未评价该节点'))
        for item in candidates:
            key=sha([item['company_id'],item['node_id'],rp,item['product'].strip().casefold()])
            prior=c.execute('SELECT id FROM sp_taxonomy_memberships WHERE assignment_key=? ORDER BY id DESC LIMIT 1',(key,)).fetchone()
            item['expected_prior_id']=prior[0] if prior else None
        result=dict(schema_version='stock.taxonomy.preview.v1',catalog_revision=rev,report_period=rp,codes=codes,node_ids=sorted(selected),node_hashes=node_hashes,input_fingerprints=inputs,candidates=candidates,gaps=gaps,cache_reused=reused,actor=actor,reason=reason,source_policy='源文支持与Agent判断，不自动verified',external_model_calls=0)
    result['preview_id']='preview_'+uuid.uuid4().hex
    result['elapsed_ms']=round((time.perf_counter()-started)*1000,3)
    digest=sha(result)
    with connect(db,True) as c:
        if catalog.revision(c)!=rev:
            raise ValueError('生成预览期间目录变化，请重试')
        insert(c,'sp_taxonomy_runs',dict(id=result['preview_id'],kind='preview',created_at=now(),request_hash=req_hash,input_hash=input_hash,payload_hash=digest,payload_json=js(result),elapsed_ms=result['elapsed_ms']))
    return {**result,'preview_hash':digest}


def apply(db,preview_id,preview_hash,expected_revision,candidate_ids=None):
    cache={}
    with connect(db,True) as c:
        run=c.execute("SELECT * FROM sp_taxonomy_runs WHERE id=? AND kind='preview'",(preview_id,)).fetchone()
        if not run or run['payload_hash']!=preview_hash:
            raise ValueError('预览不存在或哈希不符')
        payload=json.loads(run['payload_json'])
        if sha(payload)!=preview_hash:
            raise ValueError('持久预览内容损坏')
        allowed={x['candidate_id']:x for x in payload['candidates']}
        if candidate_ids is None:
            candidate_ids=[x['candidate_id'] for x in payload['candidates'] if x['status']=='source_supported']
        if not isinstance(candidate_ids,list) or not candidate_ids or len(set(candidate_ids))!=len(candidate_ids) or not set(candidate_ids)<=set(allowed):
            raise ValueError('candidate_ids须选择本预览内的非重复候选')
        selection=sha(sorted(candidate_ids))
        if run['receipt_json']:
            receipt=json.loads(run['receipt_json'])
            if receipt['selection_hash']!=selection:
                raise ValueError('同一预览已提交不同选择；需新预览')
            return {**receipt,'idempotent':True}
        rev,nodes=catalog.load(c)
        if type(expected_revision) is not int or rev!=expected_revision:
            raise ValueError('目录版本冲突')
        chosen=[allowed[x] for x in candidate_ids]
        for item in chosen:
            nid=item['node_id']
            if nid not in nodes or not catalog.active(nodes,nid) or catalog.rule_hash(nodes,nid)!=item['rule_hash']:
                raise ValueError('节点规则或归属已变化，需重新预览')
        # Related business input, not arbitrary financial revisions, invalidates previews.
        for code in {x['code'] for x in chosen}:
            sec=find_security(c,code)
            _,fp=business_input(c,sec['company_id'],payload['report_period'],cache)
            if fp!=payload['input_fingerprints'][code]:
                raise ValueError('相关业务或归档资料变化，需重新预览: '+code)
        ids=[]
        for item in chosen:
            if not item['evidence']:
                raise ValueError('没有有效原文证据，不可保存关联')
            for e in item['evidence']:
                if read_ref(c,item['company_id'],payload['report_period'],e['input_ref'],cache)['evidence_hash']!=e['evidence_hash']:
                    raise ValueError('引用证据发生变化，需重新预览')
            key=sha([item['company_id'],item['node_id'],payload['report_period'],item['product'].strip().casefold()])
            prior=c.execute('SELECT id FROM sp_taxonomy_memberships WHERE assignment_key=? ORDER BY id DESC LIMIT 1',(key,)).fetchone()
            if (prior[0] if prior else None)!=item['expected_prior_id']:
                raise ValueError('关联已被另一操作修改，需重新预览')
            mid=insert(c,'sp_taxonomy_memberships',dict(company_id=item['company_id'],node_id=item['node_id'],catalog_revision=rev,report_period=payload['report_period'],product=item['product'],stage=item['stage'],status=item['status'],reason=js({'reason':item['reason'],'role':item['role'],'stage_reason':item['stage_reason']}),actor=item['actor'],method=item['method'],rule_hash=item['rule_hash'],assignment_key=key,state='active',prior_id=prior[0] if prior else None,run_id=preview_id,created_at=now()))
            for e in item['evidence']:
                insert(c,'sp_taxonomy_evidence',dict(membership_id=mid,document_id=e['document_id'],fact_id=e['fact_id'],evidence_id=e['evidence_id'],evidence_hash=e['evidence_hash'],payload_json=js(e)))
            ids.append(mid)
        receipt=dict(preview_id=preview_id,selection_hash=selection,membership_ids=ids,count=len(ids),catalog_revision=rev,applied_at=now(),idempotent=False)
        c.execute('UPDATE sp_taxonomy_runs SET receipt_json=? WHERE id=?',(js(receipt),preview_id))
        return receipt


def remove(db,membership_id,expected_membership_id,actor,reason):
    if type(membership_id) is not int or membership_id!=expected_membership_id or not actor.strip() or not reason.strip():
        raise ValueError('须提供当前关联ID、expected_membership_id、actor及reason')
    with connect(db,True) as c:
        row=c.execute('SELECT * FROM sp_taxonomy_memberships WHERE id=?',(membership_id,)).fetchone()
        if not row:raise ValueError('未知关联')
        successor=c.execute('SELECT * FROM sp_taxonomy_memberships WHERE prior_id=?',(membership_id,)).fetchone()
        if successor:
            if successor['state']=='removed' and successor['actor']==actor and successor['reason']==reason:
                return {'removed_id':successor['id'],'idempotent':True}
            raise ValueError('关联已变化，需读取当前记录')
        if row['state']=='removed':raise ValueError('关联已撤销')
        rid='remove_'+uuid.uuid4().hex
        request=dict(membership_id=membership_id,actor=actor,reason=reason)
        insert(c,'sp_taxonomy_runs',dict(id=rid,kind='remove',created_at=now(),request_hash=sha(request),input_hash=sha(dict(row)),payload_hash=sha(request),payload_json=js(request),elapsed_ms=0))
        fields=dict(row);fields.pop('id');fields.update(prior_id=membership_id,state='removed',reason=reason,actor=actor,run_id=rid,created_at=now())
        mid=insert(c,'sp_taxonomy_memberships',fields)
        for e in c.execute('SELECT * FROM sp_taxonomy_evidence WHERE membership_id=?',(membership_id,)).fetchall():
            d=dict(e);d.pop('id');d['membership_id']=mid;insert(c,'sp_taxonomy_evidence',d)
        return {'removed_id':mid,'idempotent':False}


def query(db,codes=None,node_ids=None,match='any',stage=None,report_period='20260630',search=None,include_stale=False,include_history=False):
    if match not in ('any','all') or stage is not None and stage not in STAGES:
        raise ValueError('match为any/all；stage为actual/development/investment/unclear')
    rp=period(report_period)
    with connect(db) as c:
        rev,nodes=catalog.load(c)
        if node_ids is not None:_selected(nodes,node_ids)
        groups=[catalog.descendants(nodes,x) for x in (node_ids or [])]
        selected_securities=_codes(c,codes) if codes is not None else None
        code_scope={s['code'] for s in selected_securities} if selected_securities else None
        conditions=['m.report_period=?'];args=[rp]
        if selected_securities:
            cids=sorted({s['company_id'] for s in selected_securities})
            conditions.append('m.company_id IN ('+','.join('?' for _ in cids)+')');args.extend(cids)
        if not include_history:
            conditions.append("m.state='active' AND NOT EXISTS(SELECT 1 FROM sp_taxonomy_memberships n WHERE n.prior_id=m.id)")
        memberships=[dict(r) for r in c.execute('SELECT m.* FROM sp_taxonomy_memberships m WHERE '+' AND '.join(conditions)+' ORDER BY m.id',args)]
        items={};source_cache={};historical_nodes={rev:nodes}
        for row in memberships:
            if row['report_period']!=rp or row['state']=='removed' and not include_history:
                continue
            if stage and row['stage']!=stage:continue
            evidences=[json.loads(r[0]) for r in c.execute('SELECT payload_json FROM sp_taxonomy_evidence WHERE membership_id=?',(row['id'],))]
            nid=row['node_id']
            freshness='current';issues=[]
            if not catalog.active(nodes,nid):freshness='disabled'
            elif catalog.rule_hash(nodes,nid)!=row['rule_hash']:freshness='rule_changed'
            if freshness=='current':
                for e in evidences:
                    try:
                        if read_ref(c,row['company_id'],rp,e['input_ref'],source_cache)['evidence_hash']!=e['evidence_hash']:
                            raise ValueError('引用正文或源文件哈希发生变化')
                    except (ValueError,OSError) as exc:
                        freshness='source_changed';issues.append(str(exc));break
            if freshness!='current' and not include_stale and not include_history:continue
            path=catalog.ancestry(nodes,nid)
            row.update(path_ids=path,path=[nodes[x]['name'] for x in path],freshness=freshness,evidence=evidences,issues=issues)
            if row['catalog_revision'] not in historical_nodes:
                historical_nodes[row['catalog_revision']]=catalog.load(c,row['catalog_revision'])[1]
            original_nodes=historical_nodes[row['catalog_revision']]
            row['recorded_path']=[original_nodes[x]['name'] for x in catalog.ancestry(original_nodes,nid)]
            if row['state']=='active':row['assessment']=json.loads(row['reason'])
            for sec in c.execute('SELECT * FROM sp_securities WHERE company_id=? ORDER BY code',(row['company_id'],)):
                s=dict(sec)
                if code_scope is not None and s['code'] not in code_scope:continue
                item=items.setdefault(s['code'],dict(code=s['code'],name=s['name'],company_id=s['company_id'],memberships=[]))
                item['memberships'].append(row)
        result=[]
        for code,item in sorted(items.items()):
            ms=item['memberships'];ids={x['node_id'] for x in ms}
            if groups and not (all(bool(g&ids) for g in groups) if match=='all' else any(g&ids for g in groups)):continue
            if search and search.casefold() not in (' '.join([code,item['name']]+[m['product']+' '+' / '.join(m['path']) for m in ms])).casefold():continue
            # Scope memberships to selected union AFTER stock intersection selection.
            if groups:item['memberships']=[m for m in ms if any(m['node_id'] in g for g in groups)]
            item['original_industries']=[dict(r) for r in c.execute('''SELECT DISTINCT cl.industry,ru.name,cl.relation_kind,cl.status FROM sp_heads h
                JOIN sp_revision_classifications rc ON rc.revision_id=h.revision_id
                JOIN sp_classifications cl ON cl.id=rc.classification_id
                JOIN sp_rules ru ON ru.scheme=cl.scheme AND ru.version=cl.rule_version AND ru.industry=cl.industry
                JOIN sp_facts f ON f.id=cl.fact_id JOIN sp_documents d ON d.id=f.document_id
                JOIN sp_revision_documents rd ON rd.revision_id=h.revision_id AND rd.document_id=d.id
                WHERE h.company_id=? AND d.period_end=?''',(item['company_id'],rp))]
            result.append(item)
        counts={nid:len({i['code'] for i in result if any(nid in m['path_ids'] for m in i['memberships'])}) for nid in nodes}
        return dict(schema_version='stock.taxonomy.result.v1',catalog_revision=rev,report_period=rp,exported_at=now(),items=result,total=len(result),counts=counts,total_basis='按股票代码去重；子类数量不可加总；目录覆盖不改写原一级行业',scope_codes=codes,filters=dict(node_ids=node_ids,match=match,stage=stage,search=search,include_stale=include_stale,include_history=include_history))
