"""Public classification adapters, independent of index publication and prices."""
import threading
from collections import OrderedDict
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import digest; from guanlan_data.repositories.industry_index.inputs import readonly

def stamp(path):
    path=Path(path)
    return tuple((str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in (path,Path(str(path)+'-wal')) if p.exists())

class Catalog:
    def __init__(self,business,theme_path):
        self.business=Path(business);self.theme_path=Path(theme_path)
        self.lock=threading.Lock();self.cached=None;self.key=None;self.cache=OrderedDict()

    def read(self,codes=None):
        from guanlan_data.repositories.observer_collections.catalog import industry_collections
        working=self.business.parent/'classification_working/current.json'
        root_working=self.business.parent/'classification_working/root_current.json'
        def current_stamp():
            extra=()
            if working.exists():
                import json
                ref=json.loads(working.read_text('utf-8'));p=(working.parent/ref['file']).resolve()
                if not p.is_relative_to(working.parent.resolve()):raise ValueError('工作分类引用路径无效')
                extra=stamp(p)
            root_extra=()
            if root_working.exists():
                import json
                ref=json.loads(root_working.read_text('utf-8'));p=(root_working.parent/ref['file']).resolve()
                if not p.is_relative_to(root_working.parent.resolve()):raise ValueError('一级分类引用路径无效')
                from guanlan_data.repositories.stock_profile.working_index_snapshot import read
                from guanlan_data.repositories.stock_profile.store import connect
                with connect(self.business) as c:
                    c.execute('PRAGMA query_only=ON')
                    rules=[dict(r) for r in c.execute('SELECT * FROM sp_rules WHERE version=? ORDER BY industry',('trading_industries_30_v1',))]
                read(self.business,rules)
                root_extra=stamp(p)
            return stamp(self.business)+stamp(self.theme_path)+stamp(working)+extra+stamp(root_working)+root_extra
        current=current_stamp();key=(current,tuple(sorted(codes)) if codes is not None else None)
        with self.lock:
            if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
            groups,snapshot=industry_collections(self.business,codes=codes)
            themes=[]
            if self.theme_path.exists():
                import json
                with readonly(self.theme_path) as c:
                    for r in c.execute('SELECT r.payload_json,r.checksum FROM themes t JOIN theme_revisions r ON r.id=t.id AND r.revision=t.revision WHERE t.archived=0 ORDER BY t.id'):
                        payload=json.loads(r[0])
                        if digest(payload)!=r[1]:raise ValueError('题材修订校验失败')
                        themes.append(payload)
            clean=[{k:g.get(k) for k in ('id','name','kind','parent_id','revision','members','path')} for g in groups]
            if current_stamp()!=current:raise ValueError('分类目录读取期间已变化，请刷新；未返回混合修订')
            self.cached={'groups':clean,'themes':themes,'revision':digest([clean,themes]),'classification_snapshot':snapshot['snapshot_id']}
            self.key=key
            self.cache[key]=self.cached
            while len(self.cache)>8:self.cache.popitem(last=False)
            return self.cached
