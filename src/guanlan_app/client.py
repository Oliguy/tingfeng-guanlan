"""Versioned API client. Mutation timeouts never trigger automatic retries."""
import json
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

class SubmissionUnknown(ConnectionError): pass

READ_OPERATIONS={'observer.query','updates.list','jobs.list','jobs.get','themes.list','themes.get','themes.preview',
    'training.list','training.state','training.review','training.history'}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ConnectionError('API 地址发生重定向，请核对服务地址')

class Client:
    def __init__(self,url,token=None,*,timeout=90,legacy=False):
        self.url=url.rstrip('/');parts=urlsplit(self.url)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
            raise ValueError('服务地址须为 http(s)://主机:端口，不含密码、路径或查询参数')
        if parts.scheme=='http' and parts.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('远程连接须使用 HTTPS；也可以先建立 SSH 隧道连接本机端口')
        if legacy and parts.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('旧更新服务适配仅允许本机回环地址')
        self.token,self.timeout,self.legacy=token,timeout,legacy
        self.opener=build_opener(ProxyHandler({}),NoRedirect())
    def request(self,path,body=None,extra=None):
        headers={'Accept':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        if extra:headers.update(extra)
        content=None if body is None else json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        if content is not None:headers['Content-Type']='application/json'
        req=Request(self.url+path,data=content,headers=headers)
        try:
            with self.opener.open(req,timeout=self.timeout) as r:
                payload=r.read(32*1024*1024+1)
                if len(payload)>32*1024*1024:raise ValueError('响应超出接口限制')
                return json.loads(payload)
        except HTTPError as exc:
            if exc.code in (401,403):raise PermissionError('服务认证失败，请核对访问令牌') from None
            raise ConnectionError(f'服务返回 HTTP {exc.code}') from None
    def health(self):return self.request('/api/v1/health')
    def invoke_request(self,request):
        headers={}
        if self.legacy:
            health=self.health()
            if health.get('application')!='stock-observer-entries-v1':raise ValueError('更新服务身份不匹配')
            headers['X-Stock-Operator-Write-Token']=health['write_token']
        try:response=self.request('/api/v1/'+request['module']+'/invoke',request,headers)
        except (URLError,TimeoutError,ConnectionError,OSError,json.JSONDecodeError) as exc:
            if isinstance(exc,PermissionError):raise
            op=request['operation']
            if op not in READ_OPERATIONS:
                raise SubmissionUnknown('连接中断，提交结果待核对；请保留 request_id，勿重复提交') from None
            raise ConnectionError('服务连接中断，请检查地址和网络') from None
        if (not isinstance(response,dict) or type(response.get('ok')) is not bool or
            response.get('request_id',request['request_id'])!=request['request_id'] or
            (not self.legacy and response.get('ok') and 'request_id' not in response) or
            (response.get('ok') and 'data' not in response)):
            if request['operation'] not in READ_OPERATIONS:raise SubmissionUnknown('提交回执格式或请求编号不符，请按原 request_id 核对结果')
            raise ConnectionError('服务响应不符合 API v1')
        if not response.get('ok'):
            if response.get('error',{}).get('submission_unknown'):raise SubmissionUnknown(response['error']['message'])
            if response.get('error',{}).get('retryable') and request['operation'] not in READ_OPERATIONS:
                raise SubmissionUnknown(response['error'].get('message','保存结果待核对，请保留原请求编号'))
            raise ValueError(response.get('error',{}).get('message') or response.get('detail') or '服务请求失败')
        return response['data']
    def invoke(self,module,operation,params=None,*,request_id=None,confirmation=None):
        req={'schema_version':'operation_request_v1','module':module,'operation':operation,
             'params':params or {},'request_id':request_id or str(uuid.uuid4())}
        if confirmation is not None:req['confirmation']=confirmation
        return self.invoke_request(req)
    def query(self,module,**params):return self.invoke(module,'observer.query',params)
