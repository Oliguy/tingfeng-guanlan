"""Public read projection for all equity collections; no builders or collectors."""
import guanlan_data.repositories.observer_collections.catalog as catalog; import guanlan_data.repositories.observer_collections.store as store; import guanlan_data.repositories.observer_collections.config as config; import guanlan_data.repositories.observer_collections.quotes as quotes
from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_domain.observer_math.signals import strength
from guanlan_data.repositories.observer_calendar import load as load_calendar
import sqlite3
import json
from collections import OrderedDict
from threading import Lock
import guanlan_data.repositories.observer_series.sources as sources
from guanlan_data.repositories.observer_series.reader import Reader; from guanlan_data.repositories.observer_series.reader import unavailable
from guanlan_data.repositories.observer_collections.member_products import project as member_products
_detail_cache=OrderedDict();_cache_lock=Lock()

def _cache_identity(raw):
    from guanlan_data.repositories.observer_collections.member_signals import source_revision
    return (source_revision(raw),sources.stamp(config.results_path()),sources.stamp(config.input_paths()[0]))

def _cache_fence(raw):
    from pathlib import Path
    from guanlan_data.repositories.observer_calendar.repository import source_stamp
    paths=sources.paths()
    official={str((Path(r['ref']['support_path']).parent/item['file']).resolve())
              for r in raw['quotes'].values() for item in r['ref'].get('official_sources',())}
    return ([sources.stamp(paths[k]) for k in ('stock','etf','support','results')],
            sources.stamp(config.input_paths()[0]),source_stamp(paths['stock']),
            [sources.stamp(p) for p in sorted(official)])

def summary(module):
    try:
        with Reader() as view:rows,failures=store.summaries(config.results_path(),signal_reader=view)
    except (ValueError,OSError,sqlite3.Error) as exc:
        rows,failures=store.summaries(config.results_path())
        for row in rows:row['signal']=unavailable(str(exc))
    items=[]
    active={t['id']:t for t in catalog.list_themes()} if module=='theme' else None
    for r in rows:
        if (r['kind']=='theme')!=(module=='theme'):continue
        if active is not None and r['id'] not in active:continue
        head=active.get(r['id']) if active is not None else None
        pending=bool(head and str(head['revision'])!=str(r['revision']))
        items.append({'group_id':r['id'],'group_name':r['name'],'group_return':r['latest'].get('daily_return'),
                      'weekly_strength':r['signal'],'stats':r['stats'],'publication_id':r['publication_id'],'price_date':r['date'],
                      'kind':r['kind'],'parent_id':r['parent_id'],'revision':r['revision'],'quality':r['quality'],
                      'pending':pending,'failure':failures.get(r['id'],{}).get('message')})
    if active is not None:
        for id,head in active.items():
            if not any(r['group_id']==id for r in items):
                items.append({'group_id':id,'group_name':head['name'],'group_return':None,'weekly_strength':{},
                              'stats':{'current_listed_members':len(catalog.get(id)['members'])},'kind':'theme','parent_id':None,
                              'revision':head['revision'],'quality':'user_supplied','pending':True,'publication_id':None,
                              'price_date':None,'failure':failures.get(id,{}).get('message')})
    revision=digest(items);as_of=max((r['date'] for r in rows if (r['kind']=='theme')==(module=='theme')),default=None)
    return {'data_revision':revision,'as_of':as_of,'expected_trade_date':as_of,'focus_items':[],'items':items}

def query(module,params):
    if set(params)-{'view','if_revision','target','period','price_mode','limit','run_id','industry_id','publication_id','parent_id'}:raise ValueError('未知集合查询参数')
    view=params.get('view')
    if view=='health':return {'available':config.results_path().exists()}
    if view=='summary':
        result=summary(module)
        return {'not_modified':True,'data_revision':result['data_revision']} if params.get('if_revision')==result['data_revision'] else result
    if view not in ('detail','member_signals'):raise ValueError('未知查询')
    target=params.get('target',{})
    if set(target)!={'kind','id'} or target['kind'] not in ('group','stock'):raise ValueError('集合目标无效')
    id=params.get('parent_id',params.get('industry_id')) if target['kind']=='stock' else target['id']
    if not isinstance(id,str):raise ValueError('缺少成员所属集合')
    if module=='theme':
        current=catalog.get(id)
        if current['head_archived']:raise ValueError('此题材已归档，可从管理中恢复')
    if view=='member_signals' and (target['kind']!='group' or not params.get('publication_id')):raise ValueError('成员强弱需要固定集合发布版本')
    requested_publication=params.get('publication_id')
    if view=='detail' and target['kind']=='group' and not requested_publication and config.results_path().exists():
        requested_publication=store.current_publication(config.results_path(),id)
    cache_key=(module,id,requested_publication,params.get('period','daily'),params.get('price_mode','adjusted'))
    if view in ('detail','member_signals') and target['kind']=='group':
        with _cache_lock:cached=_detail_cache.get(cache_key)
        fence=_cache_fence(cached['raw']) if cached else None
        if cached and (cached['fence']==fence or cached['identity']==_cache_identity(cached['raw'])) and (module!='theme' or cached['theme_revision']==current['head_revision']):
            with _cache_lock:
                if cache_key in _detail_cache:_detail_cache.move_to_end(cache_key)
            result=json.loads(cached['signals_json'] if view=='member_signals' else cached['result_json'])
            if fence!=_cache_fence(cached['raw']):raise ValueError('读取期间来源发生变化，请重试')
            return result
    raw=(store.member_detail(config.results_path(),id,params['publication_id']) if view=='member_signals'
         else store.detail(config.results_path(),id,requested_publication))
    g=raw['group'];header=raw['header'];pub=raw['publication_id']
    if (g['kind']=='theme')!=(module=='theme'):raise ValueError('集合与入口不匹配')
    if view=='member_signals':
        from guanlan_data.repositories.observer_collections.member_signals import members
        return members(raw)
    period=params.get('period','daily');mode=params.get('price_mode','adjusted')
    if period not in ('daily','weekly'):raise ValueError('无效周期')
    if target['kind']=='stock':
        if not params.get('publication_id'):raise ValueError('成员行情必须固定集合发布版本')
        m=next((m for m in g['members'] if m['code']==target['id']),None)
        if not m:raise ValueError('股票不属于此版本集合')
        from guanlan_data.repositories.observer_collections.member_signals import signal; from guanlan_data.repositories.observer_collections.member_signals import configured
        ref=raw['quotes'][m['code']]['ref']
        with Reader(configured(ref)) as view:
            points,payload,events=quotes.bars(ref,header['start_date'],period,mode,header['display_dates'],series_reader=view)
            weekly_signal=signal(ref,header['actual_end'],mode=mode,reader=view)
        from guanlan_data.repositories.observer_collections.securities import identities
        identity=identities([m['code']],config.input_paths()[1],config.input_paths()[0],config.support_path()).get(m['code'],{})
        m={**m,'name':identity.get('name') or m.get('name')}
        return {'module':module,'data_revision':pub,'target':target,'period':period,'price_mode':mode,'chart_bars':points,'series':[],
                'weekly_strength':weekly_signal,
                'parent':{'id':id,'kind':'group','name':g['name']},'price_date':payload['summary']['date'],
                'stock':{**m,'quote':payload['summary'],'events':events,'metadata':payload['metadata']},
                'units':{'price':'元/股','volume':'股','amount':'元'},'header':header}
    if mode!='adjusted':raise ValueError('集合仅有连续等权指数口径')
    read_fence=_cache_fence(raw)
    products=member_products(g['members'],config.input_paths()[0],publication_id=pub)
    # Full classification evidence belongs to the individual stock detail. The
    # list projection needs names, paths and products; sending every report's
    # relations on each group open duplicated megabytes of unused evidence.
    g={**g,'members':[{**{k:v for k,v in m.items() if k!='relations'},
                       'quote':raw['quotes'][m['code']]['summary'],**products[m['code']]} for m in g['members']]}
    from guanlan_domain.observer_math.chart_metrics import enrich
    g={**g,'points':enrich(g['points'],'daily',volume_field='mean_volume'),
       'weekly_points':enrich(g['weekly_points'],'weekly',volume_field='mean_volume')}
    points=g['weekly_points'] if period=='weekly' else g['points']
    pending=module=='theme' and current['head_revision']!=g['revision']
    from guanlan_data.repositories.observer_collections.member_signals import members as member_signals; from guanlan_data.repositories.observer_collections.member_signals import configured
    saved_members=None
    try:
        first=next(iter(raw['quotes'].values()),{}).get('ref')
        with Reader(configured(first)) as view:
            saved_members=member_signals(raw,reader=view)
            weekly_signal=view.group_signal(pub,g['weekly_points']) or unavailable('集合指标尚未生成或日历已变，请本地重算')
    except (ValueError,OSError,sqlite3.Error) as exc:
        if isinstance(exc,ValueError) and '来源发生变化' in str(exc):raise
        weekly_signal=unavailable(str(exc))
    if saved_members is None:saved_members=member_signals(raw)
    result={'module':module,'data_revision':pub,'target':target,'period':period,'price_mode':mode,
            'chart_bars':[{**p,'volume':p.get('mean_volume')} for p in points],
            'series':[{**p,'synthetic_index':p.get('close'),'aggregate_amount':p.get('amount')} for p in points],
            'industry':g,'header':header,'weekly_strength':weekly_signal,'price_date':header['actual_end'],
            'pending':pending,'member_signals_available':True,'member_signal_revision':saved_members['source_revision'],'member_signals':saved_members,
            'failure':store.failure_message(config.results_path(),id)}
    identity=(saved_members['source_revision'],sources.stamp(config.results_path()),sources.stamp(config.input_paths()[0]))
    if read_fence!=_cache_fence(raw):raise ValueError('读取期间来源发生变化，请重试')
    with _cache_lock:
        _detail_cache[cache_key]={'raw':raw,'result_json':json.dumps(result,ensure_ascii=False,separators=(',',':')),
                                'signals_json':json.dumps(saved_members,ensure_ascii=False,separators=(',',':')),
                                'identity':identity,'fence':read_fence,
                                'theme_revision':current['head_revision'] if module=='theme' else None}
        if len(_detail_cache)>32:_detail_cache.popitem(last=False)
    return result
