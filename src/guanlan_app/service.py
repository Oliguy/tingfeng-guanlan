"""Use-case dispatcher. No HTML, HTTP server, SQL, or installation discovery."""
import threading
from guanlan_data.config import current
from .provider import Provider

MODULES={'home','etf','industry30','theme','movers','global','training'}

def validate(request):
    if not isinstance(request,dict) or request.get('schema_version')!='operation_request_v1':raise ValueError('请求格式无效')
    if set(request)-{'schema_version','module','operation','params','request_id','confirmation'}:raise ValueError('未知请求字段')
    if request.get('module') not in MODULES or not isinstance(request.get('operation'),str):raise ValueError('模块或操作无效')
    if not isinstance(request.get('params',{}),dict):raise ValueError('params 须为对象')
    rid=request.get('request_id')
    if not isinstance(rid,str) or not 1<=len(rid)<=128:raise ValueError('request_id 必须为 1–128 个字符')
    return request

class Service:
    def __init__(self):
        from guanlan_app.features.observer.data import DataModules
        from guanlan_data.repositories.observer.home import HomeSummary
        self.data=DataModules();self.home=HomeSummary(self.data)
        self.training=None;self.training_lock=threading.RLock();self.provider=Provider()
    def capabilities(self):
        c=current();readonly=c.raw.get('server',{}).get('read_only',False)
        local_theme=c.path('collection_root').is_relative_to(c.state)
        return {'modules':sorted(MODULES-{'global'}),'read_only':readonly,
                'training_write':not readonly,'theme_write':not readonly and (local_theme or self.provider.enabled),
                'updates':not readonly and self.provider.enabled,'connection':'local-databases',
                'demo':bool(c.raw.get('demo'))}
    def invoke(self,request):
        validate(request)
        module,op,p=request['module'],request['operation'],request.get('params',{})
        readonly=current().raw.get('server',{}).get('read_only',False)
        if op=='observer.query':
            if module=='home':return self.home.query(p)
            if module in {'etf','industry30','theme','movers'}:return self.data.query(module,p)
            raise ValueError('该模块不支持 observer.query')
        if module=='global' and op=='desktop.identify-inspector':return {'identified':False}
        if module=='global' and op=='updates.list':
            if self.provider.enabled:return self.provider.invoke(request)
            return {'items':[],'dates':{},'available':False,'message':'数据更新服务未连接'}
        if op=='jobs.list' and not self.provider.enabled:return {'items':[]}
        if module=='training':
            # Status/records may lazily initialize the dedicated user store.
            if readonly:raise ValueError('当前服务为原库只读验收模式，训练状态未启用')
            with self.training_lock:
                if self.training is None:
                    current().path('training_db').parent.mkdir(parents=True,exist_ok=True)
                    from guanlan_app.features.training.service import Service as Training
                    self.training=Training()
                return self.training.invoke(op,p,request['request_id'])
        if module=='theme' and op.startswith('themes.'):
            read=op in {'themes.list','themes.get','themes.preview'}
            if readonly and not read:raise ValueError('当前服务只读')
            c=current()
            if not c.path('collection_root').is_relative_to(c.state):
                if self.provider.enabled:return self.provider.invoke(request)
                if not read:raise ValueError('题材目录为只读数据源；请连接它的更新服务')
            from guanlan_app.features.observer_collections.commands import invoke
            return invoke(op,p,request['request_id'],_Updates(self.provider))
        if readonly:raise ValueError('当前服务只读，更新未启用')
        return self.provider.invoke(request)
    def calendar(self):
        from guanlan_data.repositories.observer_calendar import status
        return status()
    def close(self):
        self.data.close()
        if self.training:self.training.close()

class _Updates:
    def __init__(self,provider):self.provider=provider
    def submit(self,params,request_id):
        return self.provider.invoke({'schema_version':'operation_request_v1','module':'global',
              'operation':'updates.submit','params':params,'request_id':request_id})

class RemoteService:
    def __init__(self,client):self.client=client
    def capabilities(self):
        return {**self.client.health().get('capabilities',{}),'connection':'remote-api'}
    def invoke(self,request):return self.client.invoke_request(validate(request))
    def calendar(self):return self.client.request('/api/v1/calendar')['data']
    def close(self):pass
