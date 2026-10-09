"""Read-only compatibility diagnostics. No auto migration, repair, or collection."""
import json
from pathlib import Path
from contextlib import closing
from .config import current
from . import sqlite as database

def contract():return json.loads((Path(__file__).parent/'contracts/databases.v1.json').read_text('utf-8'))

def roles():
    c=current();market=c.path('market_root');collection=c.path('collection_root')
    return {'equity':market/'market/equity_daily_raw.sqlite','etf':market/'market_etf/industry_etf_observer.sqlite',
        'status':market/'market_facts/equity_status_daily.sqlite','business':c.path('business_db'),
        'support':c.path('support_db'),
        'catalog':collection/'catalog.sqlite','results':collection/'results.sqlite',
        'kph':market/'market_events/kph_limit_up.sqlite'}

def inspect():
    report={'schema_version':'guanlan.doctor.v1','contract_version':2,'roles':{},'source_write':False}
    for role,path in roles().items():
        spec=contract()['roles'][role];item={'path':str(path),'status':'compatible','issues':[],'required':spec['required']}
        report['roles'][role]=item
        if not path.is_file():item.update(status='missing',issues=['数据库不存在；请提供兼容数据或使用演示配置']);continue
        try:
            with closing(database.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
                c.execute('BEGIN')
                for table,columns in spec['tables'].items():
                    observed={r[1]:r for r in c.execute('PRAGMA table_info("'+table+'")')}
                    if not observed:item['issues'].append('缺表 '+table);continue
                    missing={r['name'] for r in columns}-set(observed)
                    if missing:item['issues'].append('缺字段 '+table+': '+', '.join(sorted(missing)))
                    for column in columns:
                        actual=observed.get(column['name'])
                        if actual is None:continue
                        if affinity(actual[2])!=affinity(column['type']):
                            item['issues'].append('字段类型不兼容 '+table+'.'+column['name'])
                        if actual[5]!=column['pk']:
                            item['issues'].append('主键不兼容 '+table+'.'+column['name'])
                if role=='etf':
                    version=c.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
                    item['version']=version[0] if version else None
                    if str(item['version']) not in {'3','4'}:item['issues'].append('ETF schema_version 仅支持 3 或 4')
                if item['issues']:item['status']='incompatible'
        except Exception as e:item.update(status='unreadable',issues=[str(e)])
    required=all(r['status']=='compatible' for r in report['roles'].values() if r['required'])
    complete=all(r['status']=='compatible' for r in report['roles'].values())
    report['status']='compatible' if complete else 'partial' if required else 'incompatible'
    report['note']='兼容表示字段和版本满足消费合同，不证明数据完整、及时或正确；缺资料不自动补采。'
    return report

def affinity(declared):
    """Compare SQLite storage affinity, allowing equivalent declared type names."""
    value=declared.upper()
    if 'INT' in value:return 'integer'
    if any(t in value for t in ('CHAR','CLOB','TEXT')):return 'text'
    if not value or 'BLOB' in value:return 'blob'
    if any(t in value for t in ('REAL','FLOA','DOUB')):return 'real'
    return 'numeric'
