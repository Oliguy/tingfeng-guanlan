"""Settings use cases. Files and source checks remain in data adapters."""
import os
from pathlib import Path
from guanlan_domain.settings import SCHEMA, DEFAULTS, preferences, data_reference, document
from guanlan_data.settings_store import SettingsStore
from guanlan_data.settings_references import check_local
from guanlan_data.layout import resolve_data_path
from .client import Client

def remote_check(data):
    health=Client(data['url'],os.environ.get(data['token_env']),timeout=8).health()
    if health.get('application')!='tingfeng-guanlan' or health.get('api_version')!=1:raise ValueError('服务不是听风观澜API v1')
    return {'status':'PASS','connection':'remote-api','version':health.get('version'),'references':[{'role':'远端服务','path':data['url'],'status':'已连接'}]}

def roles(c):
    return {'原始行情与复权':resolve_data_path(c.path('storage_root'),'market/equity_daily_raw.sqlite'),
            '经营事实':c.path('business_db'),'证券状态与限价':c.path('support_db'),
            '题材修订':c.path('collection_root')/'catalog.sqlite','统一发布':c.path('collection_root')/'results.sqlite',
            'ETF行情与映射':resolve_data_path(c.path('storage_root'),'market_etf/industry_etf_observer.sqlite')}

class SettingsService:
    def __init__(self,file,active_data,validator,references=None):
        self.active_data=data_reference(active_data);self.store=SettingsStore(file,self.active_data)
        self.validator=validator;self.references=references or []
    def read(self):
        s=self.store.read()
        return {**s,'active_data':dict(self.active_data),'restart_required':s['data']!=self.active_data,'references':self.references}
    def invoke(self,request,*,local_owner):
        action=request.get('action');revision=request.get('revision')
        if action=='check':
            if not local_owner:raise ValueError('数据引用检查请在宿主或本机代理中进行')
            return self.validator(data_reference(request.get('data',{})))
        if action not in ('preferences','data','import'):raise ValueError('设置操作无效')
        if not isinstance(revision,str):raise ValueError('请先读取当前配置版本')
        old=self.store.read()
        value={'schema_version':SCHEMA,'preferences':old['preferences'],'data':old['data']}
        if action=='preferences':value['preferences']=preferences(request.get('preferences',{}))
        elif action=='data':
            if not local_owner:raise ValueError('数据引用只能在宿主或本机代理中修改')
            value['data']=data_reference(request.get('data',{}));self.validator(value['data'])
        else:
            value=document(request.get('configuration'),self.active_data)
            if value['data']!=old['data']:
                if not local_owner:raise ValueError('数据引用只能在宿主或本机代理中修改')
                self.validator(value['data'])
        self.store.save(value,revision,recover=action=='import')
        return self.read()

def portable(c):
    from guanlan_data.config import load,settings_file
    connection=c.raw.get('connection',{})
    active={'mode':connection.get('mode','local'),'storage_root':str(c.path('storage_root')),
            'state_root':str(c.state),'training_db':str(c.path('training_db')),
            'url':connection.get('url',''),'token_env':connection.get('token_env','GUANLAN_ACCESS_TOKEN')}
    def validate(data):
        if data['mode']=='remote':return remote_check(data)
        candidate=load(c.file,ui_data_override=data)
        owned=('题材修订','统一发布') if candidate.path('collection_root').is_relative_to(candidate.state) else ()
        return check_local(roles(candidate),candidate.state,candidate.path('training_db'),owned_roles=owned)
    references=([{'role':'远端服务','path':active['url']}] if active['mode']=='remote' else [{'role':k,'path':str(v)} for k,v in roles(c).items()])
    return SettingsService(settings_file(c.file,c.raw),active,validate,references)
