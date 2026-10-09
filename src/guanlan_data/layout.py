"""Resolve logical storage roles without coupling them to physical folders.

The data owner's explicit manifest is authoritative once activated. Legacy
fixtures and portable data without a manifest retain their existing layout.
Only metadata is read here; resolving a path never opens/creates a database.
"""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = 'stock.storage-layout.v1'
MANIFEST = 'storage_layout.v1.json'

def _relative(value: str) -> PurePosixPath:
    if not isinstance(value,str) or '\\' in value or ':' in value:
        raise ValueError('storage paths must be relative POSIX paths')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('storage path escapes its root')
    return path

def load_layout(root: str | Path) -> dict[str, Any] | None:
    file=Path(root)/MANIFEST
    if not file.is_file(): return None
    value=json.loads(file.read_text('utf-8-sig'))
    if value.get('schema_version')!=SCHEMA or value.get('activation') not in {'active','prepared'}:
        raise ValueError('invalid storage layout manifest')
    for source,target in value.get('prefixes',{}).items():
        _relative(source);_relative(target)
    for source,target in value.get('aliases',{}).items():
        _relative(source);_relative(target)
    stores=value.get('stores',[])
    if len({s['id'] for s in stores})!=len(stores) or len({s['path'] for s in stores})!=len(stores):
        raise ValueError('storage IDs/physical stores must be unique')
    for store in stores: _relative(store['path'])
    return value if value['activation']=='active' else None

def resolve_data_path(root: str | Path, *parts: str) -> Path:
    root=Path(root).resolve()
    relative=_relative('/'.join(str(p).replace('\\','/') for p in parts))
    name=relative.as_posix()
    layout=load_layout(root)
    if layout:
        if name in layout.get('aliases',{}):
            name=layout['aliases'][name]
        else:
            for prefix,target in sorted(layout.get('prefixes',{}).items(),key=lambda item:len(item[0]),reverse=True):
                if name==prefix or name.startswith(prefix+'/'):
                    name=target+name[len(prefix):]
                    break
    result=root.joinpath(*_relative(name).parts).resolve()
    if not result.is_relative_to(root):raise ValueError('resolved storage path escapes its root')
    return result

def logical_relative_path(root: str | Path, path: str | Path) -> str:
    """Keep replication wire names stable when source files move locally."""
    root=Path(root).resolve();name=Path(path).resolve().relative_to(root).as_posix()
    layout=load_layout(root)
    if layout:
        for source,target in sorted(layout.get('prefixes',{}).items(),key=lambda item:len(item[1]),reverse=True):
            if name==target or name.startswith(target+'/'):
                return source+name[len(target):]
    return name

def registered_stores(root: str | Path) -> list[dict[str, Any]]:
    layout=load_layout(root)
    if not layout:return []
    return [{**s,'physical_path':str(resolve_data_path(root,s['path']))} for s in layout['stores']]

def guard_managed_store_write(root: str | Path, path: str | Path) -> None:
    """Reject retired production stores; synthetic/custom databases remain usable."""
    root=Path(root).resolve();candidate=Path(path).resolve()
    if not candidate.is_relative_to(root):return
    layout=load_layout(root)
    if not layout:return
    for name in layout.get('retired_logical_paths',[]):
        if candidate in {root.joinpath(*_relative(name).parts),resolve_data_path(root,name)}:
            raise ValueError('retired production database cannot be created or updated: '+name)

def resolve_registered_path(root: str | Path, path: str | Path) -> Path:
    """Resolve an exact historical reference without rewriting its evidence."""
    root=Path(root).resolve();candidate=Path(path).resolve();layout=load_layout(root)
    if layout:
        for source,target in layout.get('relocations',{}).items():
            old=Path(source).resolve()
            for suffix in ('','-wal','-shm'):
                if candidate==Path(str(old)+suffix):
                    return Path(str(resolve_data_path(root,target))+suffix)
    return candidate

def same_source_stamp(root: str | Path, before: list, after: list) -> bool:
    """A registered rename is equivalent only if file identity/content stat stays.

    This never substitutes old stamps: callers keep the actual current stamp
    and still reject later writes, replacements, or a changed source marker.
    """
    if len(before)!=len(after):return False
    return all(resolve_registered_path(root,a[0])==Path(b[0]).resolve() and a[1:]==b[1:]
               for a,b in zip(before,after))
