"""Versioned settings values. No persistence, installation or business-data access."""
import math
import re
from urllib.parse import urlsplit

SCHEMA = 'guanlan.settings.v1'
LINE_WIDTHS = {'zhixingWhiteWidth':2.1,'zhixingYellowWidth':2.2,'thirtyWeekWidth':1.8,'bbiWidth':1.8}
DEFAULTS = {'zoomGesture':'ctrl','nextObjectKey':'tab','showZhixing':True,'showThirtyWeek':True,'showBbi':True,
            **LINE_WIDTHS,'fontSize':13,'density':'normal','reduceMotion':False,'startPage':'home',
            'defaultPeriod':'daily','defaultPrice':'adjusted','defaultRange':150,'defaultVolume':'mean_volume'}
ENUMS = {'zoomGesture':('ctrl','alt','wheel','off'),'nextObjectKey':('tab','alt-arrow','page','off'),
         'density':('normal','compact'),'startPage':('home','industry30','etf','theme','movers','training'),
         'defaultPeriod':('daily','weekly'),'defaultPrice':('adjusted','raw'),
         'defaultVolume':('mean_volume','volume','amount','relative_volume_20')}
DATA_KEYS = {'mode','storage_root','state_root','training_db','url','token_env'}

def preferences(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('显示或看图配置含未知字段')
    result={**DEFAULTS,**value}
    for k, choices in ENUMS.items():
        if result[k] not in choices:raise ValueError('设置值无效：'+k)
    for k in ('showZhixing','showThirtyWeek','showBbi','reduceMotion'):
        if type(result[k]) is not bool:raise ValueError('开关值无效：'+k)
    if type(result['fontSize']) is not int or result['fontSize'] not in (12,13,14,16):raise ValueError('界面字号无效')
    if type(result['defaultRange']) is not int or not 20<=result['defaultRange']<=1000:raise ValueError('默认显示范围须为20至1000根')
    for k in LINE_WIDTHS:
        n=result[k]
        if type(n) not in (int,float) or not math.isfinite(n) or not .5<=n<=5:raise ValueError('辅助线粗细无效：'+k)
        result[k]=round(n,1)
    return result

def data_reference(value):
    if not isinstance(value,dict) or set(value)-DATA_KEYS:raise ValueError('数据引用含未知字段')
    result={'mode':'local','storage_root':'','state_root':'','training_db':'','url':'','token_env':'GUANLAN_ACCESS_TOKEN',**value}
    if result['mode'] not in ('local','remote'):raise ValueError('数据引用方式无效')
    for k in DATA_KEYS-{'mode'}:
        if not isinstance(result[k],str) or len(result[k])>2048 or any(ord(c)<32 for c in result[k]):raise ValueError('数据引用字段无效：'+k)
        result[k]=result[k].strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',result['token_env']):raise ValueError('令牌环境变量名称无效')
    if result['url']:
        p=urlsplit(result['url'])
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:raise ValueError('服务地址须为HTTP(S)主机地址，不含凭据或路径')
        if p.scheme=='http' and p.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('远程服务请使用HTTPS或本机SSH隧道')
        result['url']=result['url'].rstrip('/')
    if result['mode']=='remote' and not result['url']:raise ValueError('请填写远端服务地址')
    return result

def document(value, default_data=None):
    if not isinstance(value,dict) or value.get('schema_version')!=SCHEMA:raise ValueError('配置文件版本必须为 '+SCHEMA)
    if set(value)-{'schema_version','preferences','data','saved_at'}:raise ValueError('配置文件含未知字段，访问令牌不得保存到此文件')
    return {'schema_version':SCHEMA,'preferences':preferences(value.get('preferences',{})),
            'data':data_reference(value.get('data',default_data or {}))}
