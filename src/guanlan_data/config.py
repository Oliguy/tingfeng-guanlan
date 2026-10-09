"""One explicit configuration, resolved relative to its file; no installation discovery."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

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
        return [self.paths[k] for k in ('market_root','business_db','industry_db','support_db','calendar_root','analysis_root')]

_current = None

def load(path):
    file = Path(path).expanduser().resolve()
    raw = json.loads(file.read_text('utf-8-sig'))
    if raw.get('schema_version') != 'guanlan.config.v1':
        raise ValueError('配置版本必须为 guanlan.config.v1')
    unknown = set(raw)-{'schema_version','paths','server','provider','connection','demo'}
    if unknown: raise ValueError('未知配置字段：'+', '.join(sorted(unknown)))
    configured=raw.get('paths',{})
    def resolve(value):
        p=Path(value).expanduser()
        return (p if p.is_absolute() else file.parent/p).resolve()
    market=resolve(configured.get('market_root','data'))
    state=resolve(configured.get('state_root','state'))
    defaults={'market_root':market,'state_root':state,'business_db':market/'classification/business.sqlite',
        'industry_db':market/'industry/industry.sqlite','support_db':market/'industry/support.sqlite',
        'collection_root':state/'collections','calendar_root':market/'observer_calendar',
        'analysis_root':market/'analysis/stable_basis_v1','training_db':state/'training.sqlite',
        'jobs_root':state/'jobs'}
    unknown=set(configured)-set(defaults)
    if unknown:raise ValueError('未知数据角色：'+', '.join(sorted(unknown)))
    paths={k:resolve(configured[k]) if k in configured else v for k,v in defaults.items()}
    if state==market or state.is_relative_to(market) or market.is_relative_to(state):
        raise ValueError('state_root 与 market_root 必须分开，不能相互包含')
    # Writable databases are never an alias of a source database.
    for role in ('training_db','jobs_root'):
        if not paths[role].is_relative_to(state): raise ValueError(role+' 必须位于 state_root')
    for role in ('business_db','industry_db','support_db','calendar_root','analysis_root'):
        if paths[role].is_relative_to(state):raise ValueError(role+' 必须与可写状态分开')
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

def data_path(*parts): return current().path('market_root').joinpath(*parts)

def example():
    return {'schema_version':'guanlan.config.v1','paths':{'market_root':'data','state_root':'state'},
            'server':{'host':'127.0.0.1','port':18738,'public_origin':'http://127.0.0.1:18738',
                      'token_env':'GUANLAN_ACCESS_TOKEN'},
            'provider':{'enabled':False}}
