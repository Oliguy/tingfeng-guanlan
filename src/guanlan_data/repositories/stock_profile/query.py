from guanlan_data.repositories.stock_profile.store import *

SECTIONS={'security','coverage','businesses','financial_summary','business_financials','classifications','purity','cross_industry','issues','evidence_refs','source_records','financial_comparisons'}
def find_security(c,code):
 if not isinstance(code,str):raise ValueError('code must be text')
 r=c.execute('SELECT * FROM sp_securities WHERE code=?',(code,)).fetchone()
 if not r:
  rs=c.execute('SELECT DISTINCT s.* FROM sp_securities s JOIN sp_aliases a ON a.security_id=s.id WHERE a.code=?',(code,)).fetchall()
  if len(rs)>1:raise ValueError('ambiguous historical security code')
  r=rs[0] if rs else None
 if not r:raise ValueError('unknown stock profile code')
 return dict(r)
def assemble(c,cid,rid,report_period=None,as_of=None,summary_only=False):
 rev=c.execute('SELECT * FROM sp_revisions WHERE id=?',(rid,)).fetchone()
 docs=[dict(r) for r in c.execute('SELECT d.* FROM sp_documents d JOIN sp_revision_documents x ON x.document_id=d.id WHERE x.revision_id=? ORDER BY d.id',(rid,))]
 for d in docs:d['extra']=json.loads(d.pop('extra_json','{}'))
 if c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_provider_checks'").fetchone() and not as_of:
  checks={r['slot']:dict(r) for r in c.execute('SELECT * FROM sp_provider_checks WHERE company_id=?',(cid,))}
  for d in docs:
   if d['slot'] in checks:d.update(last_checked_at=checks[d['slot']]['checked_at'],last_source_at=checks[d['slot']]['acquired_at'])
 if as_of:docs=[d for d in docs if (d['published_at'] or (d['collected_at'] if d['extra'].get('provider')=='AxData' else None)) is not None and (d['published_at'] or d['collected_at'])<=as_of and (d['collected_at'] is None or d['collected_at']<=as_of)]
 target=period(report_period) if report_period else max((d['period_end'] for d in docs if d['period_end']),default=None)
 comparisons={target}
 if target:comparisons|={str(int(target[:4])-1)+target[4:],str(int(target[:4])-1)+'-12-31'}
 docs=[d for d in docs if d['period_end']==target or d['extra'].get('snapshot_v2') and d['period_end'] in comparisons or d['kind'] in ('announcement','interaction') and d['period_end'] is None]
 ids=[d['id'] for d in docs];q=','.join('?' for _ in ids) or 'NULL'
 from guanlan_data.repositories.stock_profile.source_records import query as query_source_records
 source_records=query_source_records(c,ids)
 facts=[dict(r) for r in c.execute(f'SELECT * FROM sp_facts WHERE document_id IN ({q}) ORDER BY id',ids)]
 evidence={};fe={}
 for r in c.execute(f'SELECT fe.*,e.* FROM sp_fact_evidence fe JOIN sp_facts f ON f.id=fe.fact_id JOIN sp_evidence e ON e.id=fe.evidence_id WHERE f.document_id IN ({q})',ids):
  fe.setdefault(r['fact_id'],{}).setdefault(r['field'],[]).append('sp:'+str(r['evidence_id']))
 classes=[dict(r) for r in c.execute(f'''SELECT cl.*,ru.name AS industry_name,f.status AS fact_status FROM sp_classifications cl JOIN sp_revision_classifications x ON x.classification_id=cl.id JOIN sp_rules ru ON ru.scheme=cl.scheme AND ru.version=cl.rule_version AND ru.industry=cl.industry JOIN sp_facts f ON f.id=cl.fact_id WHERE x.revision_id=? AND f.document_id IN ({q}) ORDER BY cl.id''',[rid,*ids])]
 if as_of:classes=[cl for cl in classes if cl['effective_at'] is None or cl['effective_at']<=as_of]
 financial=[]
 for cx in c.execute(f'SELECT cx.*,t.headers_json FROM sp_contexts cx LEFT JOIN sp_tables t ON t.id=cx.table_id WHERE cx.document_id IN ({q}) ORDER BY cx.id',ids):
  x=dict(cx);x['basis']=json.loads(x.pop('basis_json'));x['headers']=json.loads(x.pop('headers_json') or '[]');financial.append(x)
 contexts={x['id']:x for x in financial}
 for x in financial:x['observations']=[];x['business_links']=[]
 for r in c.execute(f'SELECT o.* FROM sp_observations o JOIN sp_contexts x ON x.id=o.context_id WHERE x.document_id IN ({q}) ORDER BY o.id',ids):
  o=dict(r);o['evidence_ref']='sp:'+str(o.pop('evidence_id'));o['inputs']=json.loads(o.pop('inputs_json') or 'null');contexts[o['context_id']]['observations'].append(o)
 for r in c.execute(f'SELECT l.* FROM sp_links l JOIN sp_contexts cx ON cx.id=l.context_id WHERE cx.document_id IN ({q})',ids):contexts[r['context_id']]['business_links'].append(dict(r))
 issues=[dict(r) for r in c.execute(f'SELECT * FROM sp_issues WHERE document_id IN ({q}) AND status=?',ids+['open'])]
 for x in issues:
  x['fields']=json.loads(x.pop('fields_json'));x['refs']=json.loads(x.pop('refs_json'))
 note_types={'DISCLOSURE_RETAINED_WITHOUT_ALLOCATION','ASSOCIATION_NOT_ATTEMPTED'}
 accounting_notes=[i for i in issues if i['type'] in note_types and i['severity']=='info']
 issues=[i for i in issues if not(i['type'] in note_types and i['severity']=='info')]
 all_actual=sorted({x['industry'] for x in classes if x['relation_kind']=='actual'})
 verified_actual=sorted({x['industry'] for x in classes if x['relation_kind']=='actual' and x['status']==x['fact_status']=='verified'})
 purity=derive_purity(financial,classes,target)
 for f in facts:
  f['products']=json.loads(f.pop('products_json'));f['activities']=json.loads(f.pop('activities_json'));f['source_ids']=fe.get(f['id'],{})
  f['extra']=json.loads(f.pop('extra_json','{}'))
 for e in c.execute(f'SELECT id,document_id,source_ref,kind,chapter,line_start,line_end,pdf_page FROM sp_evidence WHERE document_id IN ({q})',ids):
  evidence['sp:'+str(e['id'])]={k:e[k] for k in e.keys() if k!='id'}
 summary=[]
 primary_docs={d['id'] for d in docs if d['extra'].get('provider')=='AxData' and (d['slot'].startswith('axdata:financial:') or d['extra'].get('component') in ('income','balance'))}
 for cx in financial:
  if cx['dimension']=='company_metric' and cx['scope']=='consolidated' and cx['period_end']==target and (not primary_docs or cx['document_id'] in primary_docs):
   for o in cx['observations']:summary.append({**o,'context_id':cx['id'],'label':cx['label'],'period_start':cx['period_start'],'period_end':cx['period_end'],'unit':cx['unit'],'currency':cx['currency'],'scope':cx['scope']})
 fail=c.execute("SELECT error,finished_at FROM sp_runs WHERE company_id=? AND status='failed' AND started_at>=? ORDER BY id DESC LIMIT 1",(cid,rev['recorded_at'])).fetchone() if not as_of else None
 processing='needs_review' if issues else 'model_initial' if facts else 'financial_only' if financial else 'unprocessed'
 if any(d['extra'].get('provider')=='AxData' for d in docs) and not issues and (facts or financial or source_records) and all(f['status']=='source_supported' for f in facts) and all(x['status']=='source_supported' for x in financial):processing='source_supported'
 if facts and all(f['status']=='verified' for f in facts) and not issues:processing='verified_partial'  # not a declaration of complete-report review
 from guanlan_data.repositories.stock_profile.provider_storage import component_checks
 checks=[x for x in component_checks(c,cid,docs,as_of) if x.get('report_period') is None or period(x['report_period']) in comparisons]
 gaps=[x for x in checks if x['validation_status']!='valid']
 for x in gaps:issues.append(dict(document_id=x.get('effective_document_id'),object_ref=x['slot'],type='SOURCE_COMPONENT_GAP',severity='review',reason=x['slot']+' '+','.join(x['reason_codes']),fields=x['missing_fields'],refs=[],status='open'))
 from guanlan_data.repositories.stock_profile.comparisons import financial_comparisons
 return {'schema_version':VERSION,'data_revision':rev['number'],'revision_id':rid,'recorded_at':rev['recorded_at'],'financial_comparisons':financial_comparisons(financial,target),
  'security':json.loads(rev['security_json'])[0],
  'coverage':{'report_period':target,'documents':docs,'processing_status':processing,'stale':bool(fail) or any(x.get('retained_previous') for x in gaps),'last_failure':dict(fail) if fail else None,'historical_basis':'system_recorded_and_source_published' if as_of else 'current','full_report_semantic_complete':False,'component_checks':checks,'content_complete':(not gaps) if checks else None,'independently_verified':False,
              'assessments':[{'document_id':d['id'],'title':d['title'],**d['extra']['assessment']} for d in docs if d['extra'].get('assessment')],
              'accounting_notes':accounting_notes,'financial_summary_provider':'AxData' if primary_docs else None},
  'businesses':facts,'financial_summary':summary,'business_financials':financial,'classifications':classes,
  'purity':purity,'cross_industry':{'actual_industries':all_actual,'verified_actual_industries':verified_actual,'verified_hybrid':len(verified_actual)>=2,'preliminary_hybrid':len(all_actual)>=2,'development_industries':sorted({x['industry'] for x in classes if x['relation_kind']=='development'}),'investment_industries':sorted({x['industry'] for x in classes if x['relation_kind']=='investment'})},
  'issues':issues,'evidence_refs':evidence,'source_records':source_records}

def derive_purity(financial,classes,target):
 result={'status':'unknown','primary_industry':None,'shares':{},'denominator':None,'period_end':target,'threshold':'0.90','formula_version':'verified_revenue_lower_bounds_v1','input_ids':[],'reason':'没有满足同期间、同币种单位、合并范围及不重复条件的已核业务收入'}
 totals=[]
 for cx in financial:
  if cx['scope']=='consolidated' and cx['dimension']=='company_metric' and cx['period_end']==target and cx['unit'] and cx['currency']:
   for o in cx['observations']:
    if o['metric'] in ('营业收入','revenue') and o['status']=='verified' and o['value'] is not None and Decimal(o['value'])>0:totals.append((cx,o))
 if len(totals)!=1:return result
 total,obs=totals[0];den=Decimal(obs['value']);bounds={};used=[];seenfacts=set()
 for cx in financial:
  if not cx['non_overlapping'] or cx['scope']!='consolidated' or cx['dimension'] not in ('product','industry','segment') or any(cx[k]!=total[k] for k in ('period_start','period_end','unit','currency')):continue
  links=[l for l in cx['business_links'] if l['status']=='verified' and l['relation']=='direct']
  if len(links)!=1 or links[0]['fact_id'] in seenfacts:continue
  rels={x['industry'] for x in classes if x['fact_id']==links[0]['fact_id'] and x['relation_kind']=='actual' and x['status']==x['fact_status']=='verified'}
  values=[o for o in cx['observations'] if o['metric'] in ('营业收入','revenue') and o['status']=='verified' and o['value'] is not None]
  if len(rels)!=1 or len(values)!=1 or Decimal(values[0]['value'])<0:continue
  industry=next(iter(rels));bounds[industry]=bounds.get(industry,Decimal(0))+Decimal(values[0]['value']);used.append(values[0]['id']);seenfacts.add(links[0]['fact_id'])
 if sum(bounds.values(),Decimal(0))>den:return {**result,'reason':'分项已核收入大于分母，不能计算纯度'}
 shares={k:format(v/den,'f') for k,v in bounds.items()};primary=next((k for k,v in bounds.items() if v/den>=Decimal('.9')),None)
 fully=sum(bounds.values(),Decimal(0))==den
 return {**result,'status':'main_pure' if primary else 'diversified' if fully else 'unknown','primary_industry':primary,'shares':shares,'denominator':{'value':obs['value'],'unit':total['unit'],'currency':total['currency'],'context_id':total['id']},'input_ids':[obs['id'],*used],'reason':'已核收入下限达到阈值' if primary else '同口径业务拆分完整' if fully else result['reason']}

def refresh_head(c,cid,rid,number,p):
 c.execute('INSERT OR REPLACE INTO sp_heads VALUES(?,?,?,?,?,?,?,?)',(cid,rid,number,p['coverage']['report_period'],p['coverage']['processing_status'],p['purity']['status'],p['purity']['primary_industry'],js({'purity':p['purity'],'cross_industry':p['cross_industry']})))
 c.execute('DELETE FROM sp_sector_index WHERE company_id=?',(cid,))
 for cl in p['classifications']:c.execute('INSERT OR IGNORE INTO sp_sector_index VALUES(?,?,?,?)',(cid,cl['industry'],cl['relation_kind'],cl['status']))

def query(db,code,report_period=None,as_of=None,sections=None,revision=None):
 if sections is not None and (not isinstance(sections,list) or set(sections)-SECTIONS):raise ValueError('invalid sections')
 if as_of and datetime.datetime.fromisoformat(as_of).tzinfo is None:raise ValueError('as_of requires timezone')
 cutoff=stamp(as_of)
 if revision is not None and (type(revision)is not int or revision<1 or as_of):raise ValueError('revision must be positive integer; use revision OR as_of')
 with connect(db) as c:
  s=find_security(c,code);cid=s['company_id']
  r=c.execute('SELECT * FROM sp_revisions WHERE company_id=?'+(' AND recorded_at<=?' if cutoff else '')+(' AND number=?' if revision else '')+' ORDER BY number DESC LIMIT 1',[cid,*([cutoff] if cutoff else []),*([revision] if revision else [])]).fetchone()
  if not r:
   if revision:raise ValueError('unknown profile revision')
   from guanlan_data.repositories.stock_profile.provider_storage import component_checks
   checks=component_checks(c,cid,[],cutoff)
   return {'schema_version':VERSION,'data_revision':0,'security':s,'coverage':{'processing_status':'unprocessed','report_period':period(report_period),'documents':[],'component_checks':checks,'content_complete':False},'businesses':[],'issues':[],'financial_summary':[],'business_financials':[],'source_records':[],'classifications':[],'evidence_refs':{}}
  if revision:cutoff=r['recorded_at']
  result=assemble(c,cid,r['id'],report_period,cutoff)
  securities=json.loads(r['security_json']);result['security']=next((x for x in securities if x['id']==s['id']),securities[0])
  if sections is not None:result={k:v for k,v in result.items() if k not in SECTIONS or k in sections or k in ('security','coverage')}
  return result

def list_profiles(db,industry=None,stage=None,purity=None,hybrid=None,status_filter=None,offset=0,limit=30,topic=None,tags=None,tag_match='all',reason_query=None,search=None):
 if reason_query and len(reason_query)<3:
  candidates=list_profiles(db,industry=industry,stage=stage,purity=purity,hybrid=hybrid,status_filter=status_filter,limit=1,topic=topic,tags=tags,tag_match=tag_match,search=search)
  if candidates['total']>1000:raise ValueError('short reason search requires a company/tag scope of at most 1000 companies')
 if type(limit)is not int or not 1<=limit<=200 or type(offset)is not int or offset<0:raise ValueError('invalid pagination')
 if stage and stage not in ('actual','development','investment','unclear'):raise ValueError('invalid stage')
 if hybrid is not None and (not isinstance(hybrid,list) or not all(isinstance(x,str) for x in hybrid)):raise ValueError('hybrid must be an array of industry IDs')
 if topic is not None and (not isinstance(topic,str) or not topic.strip()):raise ValueError('topic must be nonempty text')
 where=[];args=[]
 for col,val in [('h.purity',purity),('h.status',status_filter)]:
  if val is not None:where.append(col+'=?');args.append(val)
 for ind in ([industry] if industry else [])+(hybrid or []):
  where.append('EXISTS(SELECT 1 FROM sp_sector_index i WHERE i.company_id=s.company_id AND i.industry=? AND i.stage=?)');args.extend([ind,stage or 'actual'])
 if stage and not industry and not hybrid:where.append('EXISTS(SELECT 1 FROM sp_sector_index i WHERE i.company_id=s.company_id AND i.stage=?)');args.append(stage)
 sql=' FROM sp_securities s LEFT JOIN sp_heads h ON h.company_id=s.company_id'+(' WHERE '+' AND '.join(where) if where else '')
 with connect(db) as c:
  if tags is not None or reason_query or search:
   from guanlan_data.repositories.stock_profile.source_records import search_clause
   if tags is not None or reason_query:
    clause,values=search_clause(c,tags or [],tag_match,reason_query);sql+=(' AND ' if where else ' WHERE ')+'('+clause+')';args.extend(values);where.append(clause)
   if search:
    if not isinstance(search,str) or len(search)>100:raise ValueError('invalid company search')
    sql+=(' AND ' if where else ' WHERE ')+"(instr(s.code,?)>0 OR instr(s.name,?)>0 OR EXISTS(SELECT 1 FROM sp_aliases a WHERE a.security_id=s.id AND instr(a.name,?)>0))";args.extend([search]*3);where.append('search')
  if topic is not None:
   if not c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_source_records'").fetchone():return {'items':[],'total':0,'offset':offset,'limit':limit,'next_offset':None,'total_basis':'current provider topic; not verified business classification'}
   sql+=(' AND ' if where else ' WHERE ')+"EXISTS(SELECT 1 FROM sp_revision_documents rd JOIN sp_source_records sr ON sr.document_id=rd.document_id WHERE rd.revision_id=h.revision_id AND sr.kind='topic' AND sr.name=?)";args.append(topic)
  dedupe=tags is not None or reason_query or search
  if dedupe:
   sql+=(' AND ' if where or topic is not None else ' WHERE ')+'s.id=(SELECT MIN(s2.id) FROM sp_securities s2 WHERE s2.company_id=s.company_id)'
  total=c.execute('SELECT COUNT(*)'+sql,args).fetchone()[0]
  rows=[dict(r) for r in c.execute('SELECT s.*,COALESCE(h.number,0) AS data_revision,h.period_end,h.status AS processing_status,h.purity,h.primary_industry,h.derived_json'+sql+' ORDER BY s.code LIMIT ? OFFSET ?',[*args,limit,offset])]
  for r in rows:r['derived']=json.loads(r.pop('derived_json') or '{}')
  return {'items':rows,'total':total,'offset':offset,'limit':limit,'next_offset':offset+limit if offset+limit<total else None,'total_basis':'unique security; industry totals must not be added'}

def evidence(db,evidence_id):
 if not re.fullmatch(r'sp:\d+',evidence_id):raise ValueError('invalid profile evidence id')
 with connect(db) as c:
  r=c.execute('SELECT e.*,d.title,d.source_url,d.published_at,d.collected_at,d.parsed_artifact,d.original_artifact,d.legacy_id FROM sp_evidence e JOIN sp_documents d ON d.id=e.document_id WHERE e.id=?',(int(evidence_id[3:]),)).fetchone()
  if not r:raise ValueError('unknown profile evidence')
  result=dict(r);result['locator']=json.loads(result.pop('locator_json'));result['text']=result.pop('quote')
  if result['text'] is None and (result['locator'].get('response_id') or result['locator'].get('object')):
   from guanlan_data.repositories.stock_profile.provider_storage import resolve as resolve_provider
   row,loc=resolve_provider(c,result['locator']);result.update(text=json.dumps(row,ensure_ascii=False),structured_source=row,locator=loc,source_status='hash_verified')
  for kind in ('parsed_artifact','original_artifact'):
   a=c.execute('SELECT * FROM sp_artifacts WHERE id=?',(result[kind],)).fetchone();result[kind]=dict(a) if a else None
  from guanlan_data.repositories.stock_profile.archives import resolve
  result['original_artifact'],result['parsed_artifact']=resolve(c,result['legacy_id'],result['original_artifact'],result['parsed_artifact'])
  a=result['parsed_artifact']
  if result['text'] is None and a and a['path'] and result['line_start']:
   p=Path(a['path'])
   if not p.is_file():result['source_status']='missing'
   elif sha(p.read_bytes())!=a['sha256']:result['source_status']='hash_mismatch'
   else:
    result['text']='\n'.join(p.read_text(encoding='utf-8-sig').splitlines()[result['line_start']-1:result['line_end']]);result['source_status']='hash_verified'
    if result['row_number']:
     from guanlan_data.repositories.md_preprocess.tables import parse_table
     table=parse_table(result['text']);row=table['matrix'][result['row_number']-1];result['table_row']=row
     tid=re.sub(r'R\d+(?:C\d+)?$','',result['source_ref']);header=c.execute('SELECT headers_json FROM sp_tables WHERE document_id=? AND source_ref=?',(result['document_id'],tid)).fetchone()
     result['headers']=json.loads(header[0]) if header else None
     result['text']=' | '.join(row)
     if result['column_number']:result['cell']=row[result['column_number']-1]
  if result['text'] is None and result['locator'].get('legacy_metric_id') and c.execute("SELECT 1 FROM sqlite_master WHERE name='metrics'").fetchone():
   r=c.execute('SELECT payload_json FROM metrics WHERE id=?',(result['locator']['legacy_metric_id'],)).fetchone()
   if r:result['structured_source']=json.loads(r[0]);result['source_status']='legacy_registered_record'
  return result
