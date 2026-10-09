"""Read an explicitly installed, hash-pinned working classification baseline.

This does not classify companies, write formal memberships, or advance a baseline
when quotes or AxData change. Installing a new baseline is a separate operation.
"""
import hashlib
import json
import re
from pathlib import Path
from guanlan_data.repositories.industry_index.config import classification_root

SCHEMA = 'industry.focus.working.v1'
ROOT_NAMES = {'光通信', '半导体', '存储', '电子元件与消费电子', '化工与新材料'}


def query(db, directory):
    root = classification_root(db)
    pointer = root / 'current.json'
    if not pointer.exists():
        return None
    ref = json.loads(pointer.read_text(encoding='utf-8'))
    file = (root / ref['file']).resolve()
    if not file.is_relative_to(root.resolve()):
        raise ValueError('工作分类路径超出数据目录')
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('工作分类快照校验失败，保留上一发布')
    data = json.loads(raw)
    if data.get('schema_version') != SCHEMA:
        raise ValueError('未知工作分类快照格式')
    nodes = {n['node_id']: n for n in directory['nodes']}
    if {nodes.get(nid, {}).get('name') for nid in data['root_ids']} != ROOT_NAMES:
        raise ValueError('工作分类范围必须为已授权的五类')
    for nid, frozen in data['nodes'].items():
        node = nodes.get(nid)
        if (not node or node['path_ids'][0] not in data['root_ids']
                or node['rule_hash'] != frozen['rule_hash']
                or node['path_ids'] != frozen['path_ids']):
            raise ValueError('子类规则或层级已变化，须单独复核分类基线')
    for row in data['rows']:
        if (row['node_id'] not in data['nodes']
                or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', row['code'])
                or row['stage'] not in {'actual', 'development', 'investment', 'unclear'}
                or row['original_decision'] not in {'include', 'pending'}
                or row['verification_status'] != '未核实'):
            raise ValueError('工作分类包含无效成员或状态')
    return {**data, 'snapshot_id': ref['sha256']}


def members(data, node, directory):
    """Project actual-operation rows, preserving pending and unverified labels."""
    nodes = {n['node_id']: n for n in directory['nodes']}
    found = {}
    for row in data['rows']:
        leaf = nodes[row['node_id']]
        if row['stage'] != 'actual' or node['node_id'] not in leaf['path_ids']:
            continue
        member = found.setdefault(row['code'], {
            'code': row['code'], 'name': row['name'], 'status': 'unverified',
            'relations': [], 'paths': [], 'classification_stage': 'actual'})
        path = leaf['path'][1:]
        if path not in member['paths']:
            member['paths'].append(path)
        member['relations'].append({
            'product': row['product'], 'stage': row['stage'],
            'reason': row['product'] + '；' + row['working_decision'],
            'original_decision': row['original_decision'],
            'verification_status': row['verification_status'],
            'source_run_id': row['source_run_id'], 'source_refs': row['refs'],
            'node_id': row['node_id'], 'classification_snapshot': data['snapshot_id']})
    return [found[code] for code in sorted(found)]
