"""Immutable compressed SDK payloads; per-response metadata never changes their hash."""
import gzip, hashlib, json, os, uuid
from functools import lru_cache
from pathlib import Path
from guanlan_data.repositories.axdata_pipeline.runtime_store import PROJECT

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_bytes(canonical(value));os.replace(tmp,path)
def data_root(db):
    db=Path(db).resolve();prod=(PROJECT/'data/business_workspace/data/business.sqlite').resolve()
    return PROJECT/'data/fundamentals' if db==prod else db.parent/'fundamentals'

@lru_cache(maxsize=8)
def _read_verified(path,expected_hash,mtime_ns,size):
    data=gzip.decompress(Path(path).read_bytes())
    if hashlib.sha256(data).hexdigest()!=expected_hash:raise ValueError('source object hash mismatch')
    return json.loads(data)
class Objects:
    def __init__(self,root):self.root=Path(root)
    def put(self,rows):
        body=canonical(rows);h=hashlib.sha256(body).hexdigest();p=self.root/'objects'/h[:2]/(h+'.json.gz')
        if p.exists():
            try:valid=hashlib.sha256(gzip.decompress(p.read_bytes())).hexdigest()==h
            except (OSError,EOFError):valid=False
            if not valid:
                # Retain corrupt bytes for diagnosis; freshly validated identical
                # content repairs all references without changing object identity.
                quarantine=p.with_name(p.name+'.corrupt.'+uuid.uuid4().hex)
                quarantine.write_bytes(p.read_bytes())
                temp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(gzip.compress(body,mtime=0));os.replace(temp,p)
        if not p.exists():
            p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
            temp.write_bytes(gzip.compress(body,mtime=0));os.replace(temp,p)
        return dict(hash=h,path=str(p),encoding='json+gzip',byte_size=p.stat().st_size,uncompressed_bytes=len(body))
    @staticmethod
    def get(ref):
        p=Path(ref['path']);s=p.stat()
        return _read_verified(str(p),ref['hash'],s.st_mtime_ns,s.st_size)
def response(job,request):
    if request.get('object'):return dict(ok=True,rows=Objects.get(request['object']))
    return json.loads((Path(job['directory'])/request['file']).read_text(encoding='utf-8'))
