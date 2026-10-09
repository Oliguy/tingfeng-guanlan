"""Only small configuration JSON files; atomic replacement and cross-process write lock."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from guanlan_domain.settings import SCHEMA, DEFAULTS, document

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    name=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as f:
            name=f.name;json.dump(value,f,ensure_ascii=False,allow_nan=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(name,path);name=None
    finally:
        if name:
            try:os.unlink(name)
            except FileNotFoundError:pass

@contextmanager
def writer_lock(path):
    lock=Path(path).with_suffix(Path(path).suffix+'.lock');lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError('其他窗口正在保存配置，请稍后重试') from None
        else:
            import fcntl
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise ValueError('其他窗口正在保存配置，请稍后重试') from None
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

class SettingsStore:
    def __init__(self,path,default_data):
        self.path=Path(path).resolve()
        if self.path.suffix.lower()!='.json':raise ValueError('设置文件须为JSON文件')
        self.default_data=default_data;self.lock=threading.RLock()
    def read(self):
        raw=self.path.read_bytes() if self.path.exists() else None
        revision=hashlib.sha256(raw).hexdigest() if raw is not None else 'absent'
        fallback={'schema_version':SCHEMA,'preferences':dict(DEFAULTS),'data':dict(self.default_data)}
        warning=None;valid=True;has_preferences=False
        if raw is not None:
            try:
                if len(raw)>131072:raise ValueError('配置文件超过128KiB')
                parsed=json.loads(raw.decode('utf-8-sig'));fallback=document(parsed,self.default_data);has_preferences='preferences' in parsed
            except (ValueError,UnicodeError,TypeError):valid=False;warning='配置文件无法读取；当前使用默认显示。请导入有效配置恢复，原文件保留。'
        return {**fallback,'revision':revision,'file':str(self.path),'valid':valid,'warning':warning,'has_saved_preferences':has_preferences}
    def save(self,value,expected_revision,*,recover=False):
        normalized=document(value,self.default_data)
        with self.lock,writer_lock(self.path):
            before=self.read()
            if before['revision']!=expected_revision:raise ValueError('配置已被其他窗口修改，请重新读取后再保存')
            if not before['valid'] and not recover:raise ValueError(before['warning'])
            if recover and not before['valid']:
                # An invalid small configuration is retained, never a database.
                old=self.path.read_bytes();invalid=self.path.with_name(self.path.name+'.invalid-'+before['revision'][:12])
                if not invalid.exists():
                    with invalid.open('xb') as f:f.write(old)
            normalized['saved_at']=datetime.now(timezone.utc).isoformat()
            atomic_json(self.path,normalized)
            return self.read()

def saved_data(path):
    """Startup overlay: a damaged settings file must not prevent entering recovery UI."""
    p=Path(path)
    if not p.exists():return None
    try:
        if p.stat().st_size>131072:return None
        return document(json.loads(p.read_text('utf-8-sig')))['data']
    except (ValueError,UnicodeError,TypeError):return None
