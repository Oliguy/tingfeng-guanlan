"""Read adapters for the unified publications; retired runs never silently fall back."""
import threading
from pathlib import Path
from guanlan_data.repositories.observer.config import settings
from guanlan_data.repositories.observer_collections.reader import query as collection_query

class DataModules:
    def __init__(self):
        self.lock=threading.RLock();self.etf=None;self.movers=None
        self.movers_lock=threading.Lock()

    def query(self,module,params):
        if module in ('industry30','theme'):
            if params.get('run_id') and not params.get('publication_id'):
                raise ValueError('旧行业run已退役；请重新选择统一发布版本')
            return collection_query(module,params)
        if module=='movers':
            with self.movers_lock:
                if self.movers is None:
                    from guanlan_data.repositories.observer_movers.reader import Reader
                    self.movers=Reader()
            return self.movers.query(params)
        if module=='etf':
            with self.lock:
                if self.etf is None:
                    from guanlan_data.repositories.observer.etf_reader import EtfReader
                    self.etf=EtfReader(Path(settings()['data_root']))
                return self.etf.query(params)
        raise ValueError('未知数据模块')

    def close(self):
        if self.etf:self.etf.close()
