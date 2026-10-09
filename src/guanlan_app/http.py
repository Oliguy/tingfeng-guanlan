"""HTTP delivery adapter: authenticated, same-origin, bounded operation envelopes."""
import gzip
import hmac
import json
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from guanlan_ui.assets import asset
from . import __version__

class Server(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=64
    def __init__(self,service,host='127.0.0.1',port=18738,*,origin=None,access_token=None,settings_service=None):
        if host not in {'127.0.0.1','localhost','::1'} and (not access_token or len(access_token)<32):
            raise ValueError('非本机服务必须设置至少 32 字符的访问令牌')
        if access_token and len(access_token)<32:raise ValueError('访问令牌至少需要 32 字符')
        if host not in {'127.0.0.1','localhost','::1'} and not origin:
            raise ValueError('远程服务必须配置 public_origin')
        if origin:
            parsed=urlsplit(origin)
            if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError('public_origin 须为浏览器实际访问的协议、主机和端口')
            if parsed.scheme=='http' and parsed.hostname not in {'127.0.0.1','localhost','::1'}:
                raise ValueError('远程浏览器使用 HTTPS 反向代理或 SSH 隧道')
            if parsed.hostname not in {'127.0.0.1','localhost','::1'} and not access_token:
                raise ValueError('远程服务必须设置访问令牌，包括回环地址上的反向代理')
        super().__init__((host,port),Handler)
        self.service=service
        self.settings_service=settings_service
        self.origin=origin or f'http://127.0.0.1:{self.server_port}'
        self.authority=urlsplit(self.origin).netloc
        self.access_token=access_token
        self.local_csrf=secrets.token_urlsafe(32)
        self.sessions={};self.lock=threading.Lock();self.attempts={}
    def session(self,headers):
        bearer=headers.get('Authorization','')
        if self.access_token and bearer.startswith('Bearer ') and hmac.compare_digest(bearer[7:],self.access_token):
            return 'bearer'
        if not self.access_token:return self.local_csrf
        try:
            cookies=SimpleCookie();cookies.load(headers.get('Cookie',''))
            sid=cookies['guanlan_session'].value
        except (KeyError,ValueError):return None
        with self.lock:
            entry=self.sessions.get(sid)
            if entry and entry[1]>time.monotonic():return entry[0]
            self.sessions.pop(sid,None)
        return None
    def close(self):
        self.server_close();self.service.close()

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    disable_nagle_algorithm=True
    def setup(self):
        super().setup();self.connection.settimeout(30)
    def log_message(self,*args):pass
    def send(self,status,payload,kind='application/json; charset=utf-8',headers=None):
        content=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        compressed=len(content)>2048 and 'gzip' in self.headers.get('Accept-Encoding','')
        if compressed:content=gzip.compress(content,compresslevel=1,mtime=0)
        self.send_response(status);self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if compressed:self.send_header('Content-Encoding','gzip');self.send_header('Vary','Accept-Encoding')
        if self.close_connection:self.send_header('Connection','close')
        for k,v in (headers or {}).items():self.send_header(k,v)
        try:self.end_headers();self.wfile.write(content)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def trusted(self):
        return (self.headers.get('Host')==self.server.authority and
                self.headers.get('Origin',self.server.origin)==self.server.origin and
                self.headers.get('Sec-Fetch-Site','none') in {'none','same-origin'})
    def reject(self,status,message):
        self.close_connection=True
        return self.send(status,{'ok':False,'detail':message})
    def do_GET(self):
        if not self.trusted():return self.reject(403,'请求来源无效')
        path=urlsplit(self.path).path
        if path.startswith('/api/'):
            session=self.server.session(self.headers)
            if not session:return self.reject(401,'请连接听风观澜服务')
            try:
                if path=='/api/v1/health':
                    return self.send(200,{'application':'tingfeng-guanlan','api_version':1,'version':__version__,
                        'write_token':session if session!='bearer' else None,
                        'capabilities':self.server.service.capabilities()})
                if path=='/api/v1/calendar':return self.send(200,{'ok':True,'data':self.server.service.calendar()})
                if path=='/api/v1/settings':
                    if not self.server.settings_service:return self.send(200,{'ok':False,'detail':'此入口没有配置保存服务'})
                    return self.send(200,{'ok':True,'data':self.server.settings_service.read()})
                return self.send(404,{'detail':'接口不存在'})
            except Exception as exc:
                return self.send(200,{'ok':False,'error':{'message':str(exc) if isinstance(exc,ValueError) else '数据读取失败，请运行 doctor 检查配置和数据库'}})
        if path in {'/','/index.html','/home','/etf','/industry30','/theme','/movers','/movers/leader','/training'}:
            return self.send(302,b'',headers={'Location':'/home/' if path in {'/','/index.html'} else path+'/'})
        try:
            if path=='/movers/leader/':content,kind=asset('index.html',module='movers',page='leader')
            elif path in {'/home/','/etf/','/industry30/','/theme/','/movers/','/training/'}:content,kind=asset('index.html',module=path.strip('/'))
            else:content,kind=asset(path.removeprefix('/'))
            return self.send(200,content,kind)
        except FileNotFoundError:return self.send(404,{'detail':'资源不存在'})
    def body(self):
        if self.headers.get('Transfer-Encoding'):raise ValueError('不支持分块请求')
        if self.headers.get_content_type()!='application/json':raise ValueError('请使用 application/json')
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=1048576:raise ValueError('请求长度无效')
        raw=self.rfile.read(size)
        if len(raw)!=size:raise ValueError('请求不完整')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('请求须为对象')
        return value
    def do_POST(self):
        if not self.trusted():return self.reject(403,'请求来源无效')
        path=urlsplit(self.path).path
        if path=='/api/v1/session':return self.login()
        session=self.server.session(self.headers)
        if not session:return self.reject(401,'会话已断开，请重新连接')
        if session!='bearer' and not hmac.compare_digest(self.headers.get('X-Stock-Operator-Write-Token',''),session):
            return self.reject(403,'会话校验失败，请刷新页面')
        try:
            request=self.body()
            if path=='/api/v1/settings':
                if not self.server.settings_service:raise ValueError('此入口没有配置保存服务')
                local_owner=self.client_address[0] in ('127.0.0.1','::1') and urlsplit(self.server.origin).hostname in ('127.0.0.1','localhost','::1')
                return self.send(200,{'ok':True,'data':self.server.settings_service.invoke(request,local_owner=local_owner)})
            route=re.fullmatch(r'/api/v1/(home|etf|industry30|theme|movers|global|training)/invoke',path)
            if not route:return self.send(404,{'detail':'接口不存在'})
            if request.get('module')!=route[1]:raise ValueError('入口与请求模块不一致')
            result=self.server.service.invoke(request)
            self.send(200,{'ok':True,'data':result,'request_id':request['request_id']})
        except (json.JSONDecodeError,ValueError) as exc:
            self.close_connection=True
            self.send(200,{'ok':False,'error':{'message':str(exc),'retryable':False}})
        except Exception as exc:
            from .client import SubmissionUnknown
            if isinstance(exc,SubmissionUnknown):message=str(exc)
            elif isinstance(exc,PermissionError):message='数据源禁止写入，请核对独立状态目录或服务权限'
            elif isinstance(exc,ConnectionError):message=str(exc)
            else:message='读取或保存失败，请运行 doctor 检查；已有记录保留，提交结果需重新读取核对'
            self.send(200,{'ok':False,'error':{'message':message,'retryable':True,
                                           'submission_unknown':isinstance(exc,SubmissionUnknown)}})
    def login(self):
        try:
            body=self.body();now=time.monotonic();key=self.client_address[0]
            with self.server.lock:
                recent=[t for t in self.server.attempts.get(key,[]) if t>now-60]
                if len(recent)>=10:return self.reject(429,'连接尝试过于频繁，请稍后重试')
                self.server.attempts[key]=recent+[now]
            token=body.get('token')
            if not isinstance(token,str) or not self.server.access_token or not hmac.compare_digest(token,self.server.access_token):
                return self.reject(401,'访问令牌无效')
            sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            with self.server.lock:
                self.server.sessions={k:v for k,v in self.server.sessions.items() if v[1]>now}
                if len(self.server.sessions)>=128:raise ValueError('会话数量已达上限')
                self.server.sessions[sid]=(csrf,now+8*3600)
            cookie=f'guanlan_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'
            if self.server.origin.startswith('https:'):cookie+='; Secure'
            self.send(200,{'ok':True},headers={'Set-Cookie':cookie})
        except (ValueError,TimeoutError):return self.reject(400,'连接请求无效')
