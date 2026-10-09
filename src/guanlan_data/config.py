"""One explicit configuration, resolved relative to its file; no installation discovery."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from .layout import resolve_data_path, load_layout, resolve_registered_path

@dataclass(frozen=True)
class Configuration:
    file: Path
    raw: dict
    paths: dict

    @property
    def state(self): return self.paths['state_root']

    def path(self, role): return self.paths[role]

    @property
    def protected(self):
        return ([self.paths[k] for k in ('market_root','business_db','classification_root','support_db','calendar_root','analysis_root')]
                +[resolve_data_path(self.path('storage_root'),'market_etf/industry_etf_observer.sqlite'),
                  resolve_data_path(self.path('storage_root'),'stable_basis'),
                  resolve_data_path(self.path('storage_root'),'zhixing'),
                  resolve_data_path(self.path('storage_root'),'basis')])

_current = None

def settings_file(file,raw=None):
    file=Path(file).resolve()
    supplied=os.environ.get('GUANLAN_SETTINGS_FILE') or (raw or {}).get('server',{}).get('settings_file')
    if not supplied:return file.with_name(file.stem+'.settings.json')
    p=Path(supplied).expanduser()
    return (p if p.is_absolute() else file.parent/p).resolve()

def load(path,*,ui_data_override=None):
    file = Path(path).expanduser().resolve()
    raw = json.loads(file.read_text('utf-8-sig'))
    if raw.get('schema_version') != 'guanlan.config.v1':
        raise ValueError('配置版本必须为 guanlan.config.v1')
    unknown = set(raw)-{'schema_version','paths','server','provider','connection','demo'}
    if unknown: raise ValueError('未知配置字段：'+', '.join(sorted(unknown)))
    configured=dict(raw.get('paths',{}))
    from .settings_store import saved_data
    overlay=ui_data_override if ui_data_override is not None else saved_data(settings_file(file,raw))
    if overlay:
        from guanlan_domain.settings import data_reference
        overlay=data_reference(overlay)
        if overlay['storage_root']:
            original=configured.get('storage_root',configured.get('market_root','data'))
            old=(file.parent/Path(original)).resolve()
            new=Path(overlay['storage_root']).expanduser()
            if not new.is_absolute():raise ValueError('总数据目录须为绝对路径')
            if new.resolve()!=old:
                for k in ('market_root','business_db','classification_root','support_db','collection_root','calendar_root','analysis_root'):configured.pop(k,None)
            configured['storage_root']=str(new)
        for k in ('state_root','training_db'):
            if overlay[k]:
                if not Path(overlay[k]).is_absolute():raise ValueError(k+' 须为绝对路径')
                configured[k]=overlay[k]
        raw={**raw,'connection':{'mode':overlay['mode'],'url':overlay['url'],'token_env':overlay['token_env']}}
    configured.pop('industry_db',None)  # retired v1 role; never resolved or opened
    def resolve(value):
        p=Path(value).expanduser()
        return (p if p.is_absolute() else file.parent/p).resolve()
    storage=resolve(configured.get('storage_root',configured.get('market_root','data')))
    grouped=load_layout(storage) is not None
    market=resolve(configured['market_root']) if 'market_root' in configured else (storage/'01_market' if grouped else storage)
    state=resolve(configured.get('state_root','state'))
    defaults={'storage_root':storage,'market_root':market,'state_root':state,
        'business_db':resolve_data_path(storage,'classification/business.sqlite'),
        'classification_root':(resolve(configured['business_db']) if 'business_db' in configured else resolve_data_path(storage,'classification/business.sqlite')).parent/'classification_working',
        'support_db':resolve_data_path(storage,'industry/support.sqlite'),
        'collection_root':resolve_data_path(storage,'industry/collections') if grouped else state/'collections',
        'calendar_root':resolve_data_path(storage,'observer_calendar'),
        'analysis_root':resolve_data_path(storage,'analysis/stable_basis_v1'),'training_db':state/'training.sqlite',
        'jobs_root':state/'jobs'}
    unknown=set(configured)-set(defaults)
    if unknown:raise ValueError('未知数据角色：'+', '.join(sorted(unknown)))
    paths={k:resolve(configured[k]) if k in configured else v for k,v in defaults.items()}
    if state==market or state.is_relative_to(market) or market.is_relative_to(state):
        raise ValueError('state_root 与 market_root 必须分开，不能相互包含')
    # Writable databases are never an alias of a source database.
    for role in ('training_db','jobs_root'):
        if not paths[role].is_relative_to(state): raise ValueError(role+' 必须位于 state_root')
    for role in ('business_db','classification_root','support_db','calendar_root','analysis_root'):
        if paths[role].is_relative_to(state):raise ValueError(role+' 必须与可写状态分开')
    setting_path=settings_file(file,raw)
    if any(setting_path.is_relative_to(p) for p in (market,paths['business_db'],paths['support_db'],paths['analysis_root'])):
        raise ValueError('设置文件必须与行情来源分开')
    return Configuration(file,raw,paths)

def configure(path):
    global _current
    _current=load(path)
    return _current

def current():
    global _current
    if _current is None:
        path=os.environ.get('GUANLAN_CONFIG')
        if not path:raise ValueError('请通过 --config 或 GUANLAN_CONFIG 指定配置文件')
        _current=load(path)
    return _current

def data_path(*parts): return resolve_data_path(current().path('storage_root'),*parts)

def example():
    return {'schema_version':'guanlan.config.v1','paths':{'market_root':'data','state_root':'state'},
            'server':{'host':'127.0.0.1','port':18738,'public_origin':'http://127.0.0.1:18738',
                      'token_env':'GUANLAN_ACCESS_TOKEN'},
            'provider':{'enabled':False}}

def source_path(path): return resolve_registered_path(current().path('storage_root'),path)
