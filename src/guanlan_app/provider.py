"""External update owner. Forwards original requests; never starts a second queue."""
import os
from .client import Client
from guanlan_data.config import current

class Provider:
    @property
    def enabled(self):return current().raw.get('provider',{}).get('enabled') is True
    def invoke(self,request):
        cfg=current().raw.get('provider',{})
        if not self.enabled:raise ValueError('尚未连接数据更新服务；请在服务端配置 provider，已有行情可继续查看')
        module,op=request['module'],request['operation']
        allowed=(module=='global' and op in {'updates.list','updates.submit','updates.retry','updates.cancel'} or
                 module in {'etf','industry30'} and (op in {'jobs.list','jobs.get','jobs.retry','data.etf.sync_daily','data.etf.backfill','stock.industry_index.build'}) or
                 module=='theme' and op.startswith('themes.'))
        if not allowed:raise ValueError('该操作不属于数据更新提供者')
        kind=cfg.get('kind','guanlan-api')
        if kind not in {'guanlan-api','legacy-loopback'}:raise ValueError('未知 provider 类型')
        token=os.environ.get(cfg.get('token_env','GUANLAN_PROVIDER_TOKEN'))
        return Client(cfg['url'],token,legacy=kind=='legacy-loopback').invoke_request(request)
