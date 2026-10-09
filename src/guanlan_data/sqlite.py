"""All runtime SQLite opens pass this policy; market connections can never write.

The configured source files are opened with mode=ro and query_only. State writes
are allowed only below state_root. No fallback creation of missing source files.
"""
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
from .config import current

def target(database, uri=False):
    value=str(database)
    if value==':memory:':return None,False
    readonly=False
    if uri:
        parsed=urlsplit(value)
        if parsed.scheme!='file' or parsed.netloc not in ('','localhost'):
            raise ValueError('只接受本地 file: SQLite URI')
        query=parse_qs(parsed.query)
        if set(query)-{'mode'}:raise ValueError('不允许额外 SQLite URI 参数')
        readonly=query.get('mode')==['ro']
        value=unquote(parsed.path)
        if len(value)>3 and value[0]=='/' and value[2]==':':value=value[1:]
    return Path(value).resolve(),readonly

def connect(database, *args, **kwargs):
    path,readonly=target(database,kwargs.get('uri',False))
    if path is not None and readonly:
        from .config import source_path
        path=source_path(path)
    if path is None:return sqlite3.connect(database,*args,**kwargs)
    cfg=current()
    protected=any(path==root or path.is_relative_to(root) for root in cfg.protected)
    if not readonly and (protected or not path.is_relative_to(cfg.state)):
        raise PermissionError('数据源只读；可写数据库必须位于配置的 state_root')
    if readonly:
        kwargs['uri']=True
        c=sqlite3.connect(path.as_uri()+'?mode=ro',*args,**kwargs)
        c.execute('PRAGMA query_only=ON')
        # Prevent future code from disabling query_only or attaching a writer.
        def authorizer(action,a,b,db,trigger):
            if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH):return sqlite3.SQLITE_DENY
            if action==sqlite3.SQLITE_PRAGMA and b is not None:
                allowed={'query_only':{'on','1'},'busy_timeout':None,'foreign_keys':{'on','1'},
                         'table_info':None,'table_xinfo':None,'index_list':None,'index_info':None,'foreign_key_list':None}
                if a.lower() not in allowed:return sqlite3.SQLITE_DENY
                values=allowed[a.lower()]
                if values is not None and b.lower() not in values:return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer)
        return c
    return sqlite3.connect(path,*args,**kwargs)
