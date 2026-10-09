"""Resolve member product references for display, without changing classifications."""
import sqlite3
import copy
from collections import OrderedDict
from threading import Lock
from pathlib import Path
from guanlan_data.repositories.stock_profile.products import summaries
_cache=OrderedDict();_lock=Lock()


def project(members, business_path,*,publication_id=None):
    files=(Path(business_path),Path(str(business_path)+'-wal'))
    key=(publication_id,tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) if p.is_file() else (str(p),None,None) for p in files)) if publication_id else None
    with _lock:
        if key is not None and key in _cache:
            _cache.move_to_end(key);return copy.deepcopy(_cache[key])
    direct = {}
    bindings = {}
    for member in members:
        code = member['code'];direct[code] = []
        for relation in member.get('relations', []):
            if relation.get('stage', relation.get('fact_stage', 'actual')) != 'actual':
                continue
            value = relation.get('product')
            if isinstance(value, str) and value.strip() and value not in direct[code]:
                direct[code].append(value.strip())
            if type(relation.get('fact_id')) is int:
                bindings.setdefault(code, []).append(relation['fact_id'])
    wanted = [m['code'] for m in members if m['code'] in bindings or not direct[m['code']]]
    try:
        profiles = summaries(business_path, wanted, fact_ids=bindings)
    except (OSError, sqlite3.Error, ValueError):
        profiles = {}
    result = {}
    for member in members:
        code = member['code'];profile = profiles.get(code, {})
        products = list(dict.fromkeys(direct[code] + profile.get('products', [])))
        basis = ('集合引用的产品/业务档案' if code in bindings or direct[code] else '当前本地业务档案')
        if profile.get('basis') == 'current_product_disclosure':
            basis = '当前本地产品收入披露项目；不代表经营阶段已核验'
        periods = '、'.join(profile.get('periods', []))
        status = ('；含未核实资料' if member.get('status') == 'unverified' or member.get('has_unverified') or
                  any(s != 'verified' for s in profile.get('statuses', [])) else '')
        result[code] = {'products': products, 'product_note': basis + ('；报告期 ' + periods if periods else '') + status}
    with _lock:
        if key is not None:
            _cache[key]=copy.deepcopy(result)
            if len(_cache)>64:_cache.popitem(last=False)
    return result
