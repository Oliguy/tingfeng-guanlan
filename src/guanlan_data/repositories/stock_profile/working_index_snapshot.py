"""Explicit frozen first-level work, merged without promoting verification.

The baseline contains the cited source fields, including complete AXDATA reasons.
It never advances when AXDATA or quotes refresh. Original response hashes remain
provenance; its frozen content and working exports are the read authority.
"""
import hashlib
import copy
import json
import re
import threading
from pathlib import Path
from guanlan_data.repositories.stock_profile.store import sha

SCHEMA = 'industry.root.working.v1'
_cache = {}
_lock = threading.RLock()

def stamp(path):
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

def read(db, rules):
    root = Path(db).resolve().parent / 'classification_working'
    pointer = root / 'root_current.json'
    if not pointer.exists(): return None
    ref = json.loads(pointer.read_text('utf-8'))
    file = (root / ref['file']).resolve()
    if not file.is_relative_to(root.resolve()): raise ValueError('一级工作分类路径超出数据目录')
    key = (stamp(pointer), stamp(file), sha(rules))
    with _lock:
        cached = _cache.get(str(pointer))
        if cached and cached[0] == key:
            data = cached[1]
        else:
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != ref['sha256']:
                raise ValueError('一级工作分类快照校验失败，保留上一发布')
            data = json.loads(raw)
            if data.get('schema_version') != SCHEMA or data.get('rule_version') != 'trading_industries_30_v1':
                raise ValueError('一级工作分类格式无效')
            if data['rules_hash'] != sha(rules): raise ValueError('一级行业规则已变化，须复核分类基线')
            companies = {c['code']:c for c in data['companies']}
            if len(companies) != len(data['companies']): raise ValueError('工作分类公司重复')
            allowed = {r['industry'] for r in rules}
            for row in data['rows']:
                if (row['code'] not in companies or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',row['code'])
                    or row['industry_id'] not in allowed or row['industry_name'] != next(r['name'] for r in rules if r['industry']==row['industry_id'])
                    or row['stage'] not in {'actual','development','investment','unclear'}
                    or row['original_decision'] not in {'include','pending','review_proposal'}
                    or row['verification_status'] != '未核实' or not row['product'].strip()):
                    raise ValueError('一级工作分类成员或经营阶段无效')
                packet = data['packets'].get(row['code'],{})
                if any(ref not in packet.get('refs',{}) for ref in row['refs']):
                    raise ValueError('一级工作分类来源引用缺失')
                if not row['refs'] and not (row['original_decision']=='review_proposal' and row.get('review_artifact')):
                    raise ValueError('一级工作分类无来源依据')
            data['snapshot_id'] = ref['sha256']
            _cache[str(pointer)] = (key,data,{})
            cached = _cache[str(pointer)]
        checks = cached[2]
        for path, expected in data['source_files'].items():
            p = Path(path); version = stamp(p)
            if checks.get(path) != version:
                if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                    raise ValueError('分类来源已变化，须重新冻结并核对基线')
                checks[path] = version
        return data

def merge(result, db, codes=None):
    data = read(db,result['rules'])
    if data is None: return result
    if result['report_period_requested']:
        raise ValueError('工作分类为冻结当前基线，不支持指定历史报告期混合读取')
    # Copy only requested members; the frozen source is still fully validated.
    selected=None if codes is None else set(codes)
    frozen = copy.deepcopy([{**g,'members':[m for m in g['members'] if selected is None or m['code'] in selected]}
                            for g in data['existing_groups']])
    if {g['id'] for g in frozen} != {r['industry'] for r in result['rules']}:
        raise ValueError('冻结的旧行业范围不完整')
    groups = {g['id']:{**g,'members':{m['code']:m for m in g['members'] if codes is None or m['code'] in codes}} for g in frozen}
    outside = {c['code'] for c in data['outside_companies']}
    for group in groups.values():
        for code in outside:
            old = group['members'].get(code)
            if old:
                if old['status']=='verified': raise ValueError('已核行业与已核目录外结论冲突，请定向复核')
                del group['members'][code]
    for row in data['rows']:
        if row['stage'] != 'actual' or (codes is not None and row['code'] not in codes): continue
        if row['code'] in outside: raise ValueError('目录外公司存在实际经营行业关系')
        member = groups[row['industry_id']]['members'].setdefault(row['code'], {
            'code':row['code'],'name':row['name'],'status':'unverified','relations':[]})
        member['has_unverified'] = True
        member['relations'].append({
            'product':row['product'],'stage':row['stage'],'status':'unverified','verification_status':'未核实',
            'reason':row['product']+'；'+row['working_decision'],'original_decision':row['original_decision'],
            'source_run_id':row['source_run_id'],'source_revision':row['source_revision'],'source_refs':row['refs'],
            'source_sha256':data['packets'].get(row['code'],{}).get('source_sha256'),
            'source_report_period':data['packets'].get(row['code'],{}).get('report_period'),
            'review_artifact':row.get('review_artifact'),'classification_snapshot':data['snapshot_id']})
    note = ('2026-09-25冻结5569家公司，实际经营覆盖5565家；2家目录外、2家仅研发等非实际阶段。'
            '新增分类按AXDATA相关字段核对，保留未核实；旧已核关系保留。多行业可重复归属，成员合计不等于去重股票数。')
    for group in groups.values():
        group['members'] = [group['members'][code] for code in sorted(group['members'])]
        group['quality'] = 'working_unverified'
        group['source'] = {'title':data['source_title'],'sha256':data['snapshot_id'],'note':note}
    actual_codes = {r['code'] for r in data['rows'] if r['stage']=='actual'}
    return {**result,'groups':list(groups.values()),'classification_quality':'incremental_working_unverified',
            'profile_companies':data['existing_profile_companies'],'latest_revision_id':data['existing_latest_revision_id'],
            'working_baseline':data['snapshot_id'],'classification_coverage':data['coverage'],
            'classification_excluded':[c for c in data['companies'] if c['code'] not in actual_codes]}
