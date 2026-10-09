import ast
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import threading
import urllib.error
import pytest
from guanlan_data.config import configure,load
from guanlan_data import sqlite as guarded
from guanlan_app.client import Client,SubmissionUnknown
from guanlan_app.http import Server

@pytest.fixture
def configured(tmp_path):
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'schema_version':'guanlan.config.v1',
        'paths':{'market_root':'sources','state_root':'user-state'}}),'utf-8')
    c=configure(cfg);c.path('market_root').mkdir();c.state.mkdir()
    return c

def test_market_reads_cannot_write_or_attach(configured):
    p=configured.path('market_root')/'sample.sqlite'
    with sqlite3.connect(p) as c:c.execute('CREATE TABLE sample(value)');c.execute('INSERT INTO sample VALUES(1)')
    before=p.read_bytes()
    with pytest.raises(PermissionError):guarded.connect(p)
    c=guarded.connect(p.as_uri()+'?mode=ro',uri=True)
    try:
        assert c.execute('SELECT value FROM sample').fetchone()==(1,)
        for sql in ['DELETE FROM sample','PRAGMA query_only=OFF',"ATTACH ':memory:' AS writable",'PRAGMA journal_mode=WAL','VACUUM']:
            with pytest.raises(sqlite3.Error):c.execute(sql)
    finally:c.close()
    assert p.read_bytes()==before
    with pytest.raises(PermissionError):guarded.connect(configured.file.parent/'outside.sqlite')
    with guarded.connect(configured.state/'state.sqlite') as c:c.execute('CREATE TABLE user_note(value)')

def test_config_paths_follow_config_not_cwd(configured,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    c=load(configured.file)
    assert c.state==configured.file.parent/'user-state'
    raw=dict(c.raw);raw['paths']={'state_root':'sources/state','market_root':'sources'}
    configured.file.write_text(json.dumps(raw),'utf-8')
    with pytest.raises(ValueError,match='必须分开'):load(configured.file)

class Echo:
    def __init__(self):self.requests=[]
    def capabilities(self):return {'modules':['home'],'updates':True}
    def invoke(self,r):self.requests.append(r);return {'request_id':r['request_id']}
    def calendar(self):return {'date':'synthetic'}
    def close(self):pass

@contextmanager
def running(token=None,service=None):
    s=Server(service or Echo(),port=0,access_token=token)
    t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
    try:yield s
    finally:s.shutdown();s.close();t.join(3)

def raw(s,method,path,body=None,headers=None):
    c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=3)
    try:
        c.request(method,path,body,headers or {});r=c.getresponse();data=r.read()
        return r.status,dict(r.getheaders()),json.loads(data) if data else None
    finally:c.close()

def test_auth_cookie_csrf_and_origin():
    token='test-token-'+'x'*40
    with running(token) as s:
        assert raw(s,'GET','/api/v1/health')[0]==401
        status,headers,_=raw(s,'POST','/api/v1/session',json.dumps({'token':token}),{'Content-Type':'application/json'})
        assert status==200 and 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
        cookie=headers['Set-Cookie'].split(';')[0]
        _,_,health=raw(s,'GET','/api/v1/health',headers={'Cookie':cookie})
        request={'schema_version':'operation_request_v1','module':'home','operation':'observer.query','params':{},'request_id':'one'}
        h={'Cookie':cookie,'Content-Type':'application/json'}
        assert raw(s,'POST','/api/v1/home/invoke',json.dumps(request),h)[0]==403
        h['X-Stock-Operator-Write-Token']=health['write_token']
        assert raw(s,'POST','/api/v1/home/invoke',json.dumps(request),h)[2]['ok']
        h['Origin']='https://unrelated.invalid'
        assert raw(s,'POST','/api/v1/home/invoke',json.dumps(request),h)[0]==403
        assert len(s.service.requests)==1
        client=Client(s.origin,token)
        assert client.invoke('home','observer.query',request_id='two')=={'request_id':'two'}
        assert client.health()['write_token'] is None

def test_remote_bind_requires_token_and_secure_origin():
    with pytest.raises(ValueError):Server(Echo(),host='0.0.0.0',port=0)
    with pytest.raises(ValueError):Server(Echo(),host='127.0.0.1',port=0,origin='https://guanlan.example.invalid')
    with pytest.raises(ValueError):Client('http://192.0.2.1:18738','token')
    with pytest.raises(ValueError):Client('https://user:password@example.invalid')

def test_write_transport_failure_keeps_request_identity(monkeypatch):
    c=Client('http://127.0.0.1:1');calls=[]
    def failed(path,body,extra):calls.append(body);raise urllib.error.URLError('offline')
    monkeypatch.setattr(c,'request',failed)
    with pytest.raises(SubmissionUnknown):c.invoke('global','updates.submit',{'mode':'all'},request_id='original-id')
    assert len(calls)==1 and calls[0]['request_id']=='original-id'
    with pytest.raises(ConnectionError):c.query('etf',view='summary')

def test_malformed_write_receipt_is_unknown(monkeypatch):
    c=Client('http://127.0.0.1:1')
    for response in ({'ok':True,'data':{},'request_id':'wrong'}, {'ok':True,'data':{}},
                     {'ok':False,'error':{'retryable':True,'message':'unknown'}}):
        monkeypatch.setattr(c,'request',lambda *a: response)
        with pytest.raises(SubmissionUnknown):c.invoke('global','updates.submit',request_id='expected')
    def malformed(*args):raise json.JSONDecodeError('invalid','',0)
    monkeypatch.setattr(c,'request',malformed)
    with pytest.raises(SubmissionUnknown):c.invoke('global','updates.submit',request_id='expected')

def test_api_proxy_preserves_request_and_function_result():
    from guanlan_app.service import RemoteService
    with running('s'*40) as upstream:
        remote=RemoteService(Client(upstream.origin,'s'*40))
        with running(service=remote) as local:
            _,_,h=raw(local,'GET','/api/v1/health')
            body={'schema_version':'operation_request_v1','module':'global','operation':'updates.submit',
                  'params':{'mode':'all'},'request_id':'cross-computer-original'}
            result=raw(local,'POST','/api/v1/global/invoke',json.dumps(body),
              {'Content-Type':'application/json','X-Stock-Operator-Write-Token':h['write_token']})
            assert result[2]['data']['request_id']=='cross-computer-original'
            assert upstream.service.requests==[body]

def test_layer_dependencies_are_enforced():
    root=Path(__file__).parents[1]/'src'
    for layer in ('guanlan_ui','guanlan_domain','guanlan_data','guanlan_app'):
        for p in (root/layer).rglob('*.py'):
            code=p.read_text('utf-8');tree=ast.parse(code)
            imports=[]
            for n in ast.walk(tree):
                if isinstance(n,ast.ImportFrom):imports.append(n.module or '')
                elif isinstance(n,ast.Import):imports.extend(a.name for a in n.names)
            if layer=='guanlan_ui':assert not any(x.startswith(('guanlan_data','guanlan_app','sqlite3')) for x in imports),p
            if layer=='guanlan_data':assert not any(x.startswith(('guanlan_app','guanlan_ui')) for x in imports),p
            if layer=='guanlan_domain':assert not any(x.startswith(('guanlan_data','guanlan_app','guanlan_ui')) for x in imports),p
            if layer=='guanlan_app':assert 'sqlite3.connect(' not in code and 'SELECT ' not in code,p
            for module in imports:
                if module.startswith('guanlan_'):
                    target=root.joinpath(*module.split('.'))
                    assert target.with_suffix('.py').is_file() or (target/'__init__.py').is_file(),(p,module)

def test_demo_is_synthetic_and_does_not_overwrite(tmp_path):
    from guanlan_data.demo import create
    from guanlan_data.doctor import inspect
    path=create(tmp_path/'new-demo');assert inspect()['status']=='compatible'
    from guanlan_app.service import Service
    s=Service()
    try:
        request={'schema_version':'operation_request_v1','module':'training','operation':'training.start',
                 'params':{'length':60},'request_id':'synthetic-round'}
        state=s.invoke(request);again=s.invoke(request)
        assert state['id']==again['id'] and state['status']=='active'
        s.close();s=Service()
        reopened=s.invoke({**request,'operation':'training.state','params':{'id':state['id']},'request_id':'resume-round'})
        assert reopened['id']==state['id']
        assert s.capabilities()['demo']
        theme={'schema_version':'operation_request_v1','module':'theme','operation':'themes.preview',
               'params':{'payload':{'name':'合成题材','members':[{'code':'600001.SH','paths':[['演示']]}]}},'request_id':'theme-preview'}
        draft=s.invoke(theme)
        save={**theme,'operation':'themes.save','params':{'draft':draft},'request_id':'theme-save'}
        receipt=s.invoke(save);assert s.invoke(save)==receipt
        s.close();s=Service()
        detail=s.invoke({**theme,'operation':'themes.get','params':{'id':receipt['id']}})
        assert detail['name']=='合成题材' and detail['head_revision']==1
        s.invoke({**theme,'operation':'themes.archive','params':{'id':receipt['id'],'expected_revision':1},'request_id':'theme-archive'})
        restored=s.invoke({**theme,'operation':'themes.restore','params':{'id':receipt['id'],'expected_revision':2},'request_id':'theme-restore'})
        assert restored['revision']==3 and not restored['archived']
    finally:s.close()
    with pytest.raises(FileExistsError):create(Path(path).parent)
    from guanlan_data.doctor import roles
    with sqlite3.connect(roles()['equity']) as c:
        c.execute('DROP TABLE equity_master')
        c.execute('CREATE TABLE equity_master(ts_code BLOB)')
    broken=inspect()
    assert broken['status']=='incompatible'
    assert '字段类型不兼容 equity_master.ts_code' in broken['roles']['equity']['issues']
    assert '主键不兼容 equity_master.ts_code' in broken['roles']['equity']['issues']
