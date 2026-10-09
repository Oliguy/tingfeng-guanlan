"""Owned files, single writer, pinned generations and bounded reclamation."""
from __future__ import annotations
import hashlib
import json
import os
import re
import uuid
from functools import wraps
from pathlib import Path
from guanlan_data.repositories.guanlan_backend.analysis_cache.contract import SCHEMA_VERSION

class CacheError(RuntimeError):
    pass

def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))

def atomic_json(path: Path, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    temp = path.with_name(path.name + '.tmp')
    with temp.open('wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)

def sha256(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 ** 2), b''):
            h.update(block)
    return h.hexdigest()

class FileLock:

    def __init__(self, path: Path, *, shared=False, create=False):
        self.file = path.open('a+b' if create else 'rb' if shared else 'r+b')
        if create and self.file.seek(0, 2) == 0:
            self.file.write(b' ')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import ctypes
                import msvcrt
                from ctypes import wintypes

                class Overlapped(ctypes.Structure):
                    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
                self._overlapped = Overlapped()
                self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                self._handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.file.fileno()))
                self._kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
                self._kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
                if not self._kernel.LockFileEx(self._handle, 1 if shared else 3, 0, 1, 0, ctypes.byref(self._overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                import fcntl
                fcntl.flock(self.file, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise CacheError(f'File is in use: {path}') from None

    def close(self):
        if self.file.closed:
            return
        if os.name == 'nt':
            import ctypes
            self._kernel.UnlockFileEx(self._handle, 0, 1, 0, ctypes.byref(self._overlapped))
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

def owned_path(root: Path, relative: str) -> Path:
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()) or p == root.resolve():
        raise CacheError('Cache path leaves its owned directory')
    return p

def prepare_root(root: str | Path, source: Path) -> Path:
    root = Path(root).resolve()
    if source.is_relative_to(root) or root.is_relative_to(source):
        raise CacheError('Analysis directory overlaps the original database')
    marker = root / 'owner.json'
    if marker.exists():
        if read_json(marker) != {'owner': SCHEMA_VERSION}:
            raise CacheError('Analysis directory has an unknown owner')
    else:
        if root.exists() and any(root.iterdir()):
            raise CacheError('Refusing to adopt or clean a nonempty existing directory')
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(marker, {'owner': SCHEMA_VERSION})
    (root / 'objects').mkdir(exist_ok=True)
    (root / 'generations').mkdir(exist_ok=True)
    return root

def directory_bytes(root: Path) -> int:
    return sum((p.stat().st_size for p in root.rglob('*') if p.is_file()))

def object_entry(root: Path, path: Path, **extra) -> dict:
    return {'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha256(path), **extra}

def object_path(root: Path, suffix: str) -> Path:
    return owned_path(root, 'objects/' + uuid.uuid4().hex + suffix)

def report_operation_failure(function):
    """Also report failures occurring before a new source snapshot/journal exists."""

    @wraps(function)
    def wrapped(source_path, root, *args, **kwargs):
        try:
            return function(source_path, root, *args, **kwargs)
        except Exception as exc:
            cache_root = Path(root).resolve()
            try:
                if (cache_root / 'owner.json').exists() and read_json(cache_root / 'owner.json') == {'owner': SCHEMA_VERSION} and (cache_root / 'run.json').exists():
                    with FileLock(cache_root / 'writer.lock'):
                        run = read_json(cache_root / 'run.json')
                        active = read_active(cache_root)
                        committed = active and active['generation_id'] == run['generation_id'] and (run['phase'] == 'verified')
                        if active and (not committed):
                            run['source_error'] = f'{type(exc).__name__}: {exc}'
                            atomic_json(cache_root / 'run.json', run)
            except (OSError, CacheError, ValueError):
                pass
            raise
    return wrapped

def generation_entries(manifest: dict):
    return list(manifest['partitions'].values()) + [manifest[k] for k in ('anchors', 'symbols', 'state')]

def read_active(root: Path):
    p = root / 'active.json'
    if not p.exists():
        return None
    active = read_json(p)
    manifest_path = owned_path(root, active['manifest'])
    if sha256(manifest_path) != active['sha256']:
        raise CacheError('Active generation manifest hash does not match')
    manifest = read_json(manifest_path)
    if manifest['generation_id'] != active['generation_id'] or manifest['schema_version'] != SCHEMA_VERSION:
        raise CacheError('Active generation identity or schema does not match')
    return manifest

def collect_unreferenced(root: Path):
    """Remove only files produced here; a shared reader lock protects its generation."""
    if read_json(root / 'owner.json') != {'owner': SCHEMA_VERSION}:
        raise CacheError('Unknown cache owner')
    kept = set()
    active = read_active(root)
    active_id = active['generation_id'] if active else None
    for path in (root / 'generations').glob('*.json'):
        if not re.fullmatch('[0-9a-f]{32}\\.json', path.name):
            continue
        manifest = read_json(path)
        if path.stem == active_id:
            kept.update((e['path'] for e in generation_entries(manifest)))
            continue
        try:
            lock = FileLock(path.with_suffix('.lock'))
        except CacheError:
            kept.update((e['path'] for e in generation_entries(manifest)))
            continue
        try:
            path.unlink()
        except PermissionError:
            kept.update((e['path'] for e in generation_entries(manifest)))
        finally:
            lock.close()
        if not path.exists():
            path.with_suffix('.lock').unlink(missing_ok=True)
    run_path = root / 'run.json'
    if run_path.exists():
        run = read_json(run_path)
        if run.get('phase') != 'activated':
            kept.update((e['path'] for e in run.get('partitions', {}).values()))
            kept.update(run.get('extra_paths', []))
    for p in (root / 'objects').iterdir():
        if re.fullmatch('[0-9a-f]{32}\\.(?:parquet|json)', p.name):
            if p.relative_to(root).as_posix() not in kept:
                owned_path(root, p.relative_to(root).as_posix()).unlink()
