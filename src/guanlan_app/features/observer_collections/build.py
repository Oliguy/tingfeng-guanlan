"""Explicit local publication. Reads stocks once, calculates each unique member once per collection."""
from datetime import date
from pathlib import Path
import time
from guanlan_data.repositories.industry_index.inputs import read_market; from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_data.repositories.industry_index.support import repair_inputs; from guanlan_data.repositories.industry_index.support import valuation_quotes
from guanlan_domain.industry_index.engine import calculate_group; from guanlan_domain.industry_index.engine import METHOD
from guanlan_domain.industry_index.indicators import enrich; from guanlan_domain.industry_index.indicators import provenance
from guanlan_data.repositories.industry_index.store import writer_lock
import guanlan_data.repositories.observer_collections.catalog as catalog; import guanlan_data.repositories.observer_collections.store as store; import guanlan_data.repositories.observer_collections.config as config
from guanlan_data.repositories.observer_collections.quotes import references
from guanlan_data.repositories.observer_collections.inputs import ReadOnlySupport

def collections(business_path,catalog_path):
    groups,snapshot=catalog.industry_collections(business_path)
    for head in catalog.list_themes(catalog_path):
        theme=catalog.get(head['id'],catalog_path)
        groups.append({**theme,'kind':'theme','parent_id':None,'quality':'user_supplied'})
    return groups,snapshot

def calculate_publish(groups,market,refs,output,catalog_path,header,progress=None,calendar=None):
    derived={**market,'quotes':valuation_quotes(market)};published=[];failed=[]
    for definition in groups:
        try:
            group=calculate_group(definition,derived)
            if group['stats']['unknown_identity_members']:raise ValueError('证券身份未完整核实')
            if group['stats']['gap_sessions']:
                holes=[p for p in group['calculation_points'] if p['status']=='gap']
                sample=holes[0] if holes else {}
                raise ValueError('历史行情存在未解释缺口：'+sample.get('trade_date','')+' '+','.join(m['code'] for m in sample.get('missing',[])[:8]))
            group=enrich(group,calendar=calendar)
            group.update({k:definition[k] for k in ('kind','parent_id','revision','quality','tags','source','path') if k in definition})
            result=store.publish(output,definition,group,header,refs,catalog_path=catalog_path if definition['kind']=='theme' else None)
            published.append(result)
        except Exception as exc:
            store.failure(output,definition,str(exc));failed.append({'id':definition['id'],'name':definition['name'],'message':str(exc)})
        if progress:progress({'completed':len(published)+len(failed),'total':len(groups),'id':definition['id']})
    return {'published':published,'failed':failed,'status':'partial' if failed and published else 'failed' if failed else 'succeeded','as_of':header['actual_end']}

def build(*,end_date=None,start_date=None,online=False,collection_ids=None,business_path=None,market_path=None,output=None,catalog_path=None,support_path=None,progress=None):
    start=time.perf_counter();business,market_db=config.input_paths()
    business_path=Path(business_path or business);market_path=Path(market_path or market_db)
    output=Path(output or config.results_path());catalog_path=Path(catalog_path or config.catalog_path());support_path=Path(support_path or config.support_path())
    if output.resolve() in {p.resolve() for p in (business_path,market_path,catalog_path,support_path)}:raise ValueError('结果库不得覆盖输入库')
    with writer_lock(output):
        groups,snapshot=collections(business_path,catalog_path)
        if collection_ids is not None:
            ids=set(collection_ids);groups=[g for g in groups if g['id'] in ids]
            if ids!={g['id'] for g in groups}:raise ValueError('集合不存在或已归档')
        codes=sorted({m['code'] for g in groups for m in g['members']})
        market=read_market(market_path,codes,end_date=end_date or date.today().isoformat(),start_date=start_date)
        repair_inputs(market,support_path,online=online,client_factory=None if online else ReadOnlySupport)
        refs=references(market,market_path,support_path)
        header={k:market[k] for k in ('start_date','requested_end','actual_end','display_dates','calendar_hash','calendar_source','market_hash','market_vintage','support_hash')}
        header.update(method_version=METHOD,publication_format='collection_references_v1',classification_snapshot=snapshot['snapshot_id'],
                      working_baseline=snapshot.get('working_baseline'),classification_coverage=snapshot.get('classification_coverage'),
                      classification_excluded=snapshot.get('classification_excluded'),
                      focus_classification_snapshot=snapshot.get('focus_snapshot'),
                      units={'price':'指数点','volume':'股/只','amount':'元'},etf_components=provenance(),external_model_calls=0,
                      quote_reference_hash=digest({c:r['id'] for c,r in refs.items()}),calculation_start=market['calculation_dates'][0])
        # Classification is mutable. Do not activate a build from a mixed membership read.
        current,_=catalog.industry_collections(business_path)
        current_revisions={g['id']:g['revision'] for g in current}
        if any(g['kind']!='theme' and current_revisions.get(g['id'])!=g['revision'] for g in groups):raise ValueError('行业分类已变化，请重新计算')
        from guanlan_data.repositories.observer_calendar import load as load_calendar
        calendar=load_calendar(market_path)
        header['observer_calendar_revision']=calendar.revision
        result=calculate_publish(groups,market,refs,output,catalog_path,header,progress,calendar=calendar)
        result.update(unique_stocks=len(codes),elapsed_seconds=round(time.perf_counter()-start,2),online=online)
        return result
