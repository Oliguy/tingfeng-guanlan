"""Read-only verification of a proposed source. No imports from application/UI."""
from contextlib import closing
from pathlib import Path
import sqlite3

def check_local(references,state,training,*,owned_roles=()):
    state=Path(state).resolve();training=Path(training).resolve()
    if not training.is_relative_to(state):raise ValueError('训练库必须位于用户状态目录内')
    checked=[]
    for role,path in references.items():
        p=Path(path).resolve()
        if p==training or (role not in owned_roles and (p.is_relative_to(state) or (p.is_dir() and state.is_relative_to(p)))):
            raise ValueError('来源与用户状态必须分开：'+role)
        if not p.exists():raise ValueError('数据引用不存在：'+role+' · '+str(p))
        if p.suffix.lower()=='.sqlite':
            with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as c:
                c.execute('PRAGMA query_only=ON')
                if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():raise ValueError('来源库没有业务表：'+role)
        checked.append({'role':role,'path':str(p),'status':'可读取'})
    return {'status':'PASS','references':checked,'source_writes':False}
