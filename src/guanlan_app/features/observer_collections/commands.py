"""Bounded explicit catalog operations; quote collection remains in the global updater."""
from datetime import date
import guanlan_data.repositories.observer_collections.catalog as catalog

def invoke(operation,params,request_id,updates):
    if operation=='themes.list' and not params:return {'items':catalog.list_themes(include_archived=True)}
    if operation=='themes.get' and set(params)=={'id'}:return catalog.get(params['id'])
    if operation=='themes.preview' and set(params)<={'payload','id','expected_revision'}:
        return catalog.preview(params.get('payload'),theme_id=params.get('id'),expected_revision=params.get('expected_revision',0))
    if operation=='themes.save' and set(params)=={'draft'}:return catalog.commit(params['draft'],request_id)
    if operation in ('themes.archive','themes.restore') and set(params)=={'id','expected_revision'}:
        return catalog.archive(params['id'],params['expected_revision'],request_id,restore=operation.endswith('restore'))
    if operation=='themes.apply' and set(params)=={'id','expected_revision'}:
        current=catalog.get(params['id'])
        if current['head_archived'] or current['head_revision']!=params['expected_revision']:raise ValueError('目录已变化，请刷新后应用')
        return updates.submit({'mode':'collection','target':params['id'],'end_date':date.today().isoformat()},request_id)
    raise ValueError('题材操作参数无效')
