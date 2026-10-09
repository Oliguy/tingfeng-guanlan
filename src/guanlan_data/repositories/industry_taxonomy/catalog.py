"""Small immutable catalog snapshots. Only this namespace is writable here."""
import copy
import json
import uuid
from pathlib import Path

from guanlan_data.repositories.stock_profile.store import connect; from guanlan_data.repositories.stock_profile.store import insert; from guanlan_data.repositories.stock_profile.store import js; from guanlan_data.repositories.stock_profile.store import now; from guanlan_data.repositories.stock_profile.store import sha

ROOT_RULES = Path(__import__('guanlan_data').__file__).parent / 'rules' / 'industries_30.v1.json'
SEED = Path(__file__).with_name('seed.json')


def frozen_roots():
    return json.loads(ROOT_RULES.read_text(encoding='utf-8-sig'))['sectors']


def revision(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='sp_taxonomy_revisions'").fetchone():
        return 0
    return c.execute('SELECT COALESCE(MAX(id),0) FROM sp_taxonomy_revisions').fetchone()[0]


def load(c, rev=None):
    current = revision(c)
    rev = current if rev is None else rev
    if type(rev) is not int or rev < 1 or rev > current:
        raise ValueError('目录未初始化或版本不存在；使用 manage(action=initialize, expected_revision=0)')
    nodes = {}
    for row in c.execute('SELECT * FROM sp_taxonomy_nodes WHERE revision_id=? ORDER BY position,node_id', (rev,)):
        r = dict(row)
        r['aliases'] = json.loads(r.pop('aliases_json'))
        r['rule'] = json.loads(r.pop('rule_json'))
        r['enabled'] = bool(r['enabled'])
        nodes[r['node_id']] = r
    return rev, nodes


def ancestry(nodes, nid):
    result = []
    while nid is not None:
        if nid in result or nid not in nodes:
            raise ValueError('目录存在循环或无效父节点')
        result.append(nid)
        nid = nodes[nid]['parent_id']
    return list(reversed(result))


def active(nodes, nid):
    return all(nodes[x]['enabled'] for x in ancestry(nodes, nid))


def rule_hash(nodes, nid):
    # Labels and sort order never change classification meaning; ancestry/rules do.
    return sha([{'id':x, 'rule':nodes[x]['rule']} for x in ancestry(nodes, nid)])


def descendants(nodes, nid):
    if nid not in nodes:
        raise ValueError('未知节点: ' + nid)
    return {x for x in nodes if nid in ancestry(nodes, x)}


def validate(nodes):
    roots = {r['id']:r for r in frozen_roots()}
    actual = {k for k,v in nodes.items() if v['parent_id'] is None}
    if actual != set(roots):
        raise ValueError('一级行业必须恰为固定 I01..I30')
    sibling_names = set()
    for nid, n in nodes.items():
        if not isinstance(n['name'], str) or not n['name'].strip() or len(n['name']) > 100:
            raise ValueError('名称应为1至100字')
        if type(n['position']) is not int or not isinstance(n['enabled'], bool):
            raise ValueError('position应为整数；enabled应为布尔值')
        path = ancestry(nodes, nid)
        if len(path) > 3:
            raise ValueError('首版最多三级')
        if nid in roots:
            r = roots[nid]
            if n['name'] != r['name'] or not n['enabled'] or n['rule'] != {'include':r['includes'], 'exclude':r['excludes'], 'terms':[]}:
                raise ValueError('不能修改固定一级定义')
        if not isinstance(n['aliases'], list) or not all(isinstance(x,str) and x.strip() for x in n['aliases']):
            raise ValueError('aliases应为非空字符串数组')
        rule = n['rule']
        if not isinstance(rule,dict) or set(rule)-{'include','exclude','terms','context_terms'} or not all(isinstance(rule.get(k),str) and rule[k].strip() for k in ('include','exclude')):
            raise ValueError('规则须提供纳入/排除说明和terms数组')
        for key in ('terms','context_terms'):
            values = rule.get(key,[])
            if not isinstance(values,list) or not all(isinstance(x,str) and x.strip() for x in values):
                raise ValueError('匹配词须为字符串数组')
        key = (n['parent_id'],n['name'].strip().casefold())
        if n['enabled'] and key in sibling_names:
            raise ValueError('同一父节点下名称重复')
        if n['enabled']:
            sibling_names.add(key)
            if not active(nodes,nid):
                raise ValueError('启用节点的父节点必须启用')


def save(c, nodes, prior, reason, actor):
    validate(nodes)
    clean = [{k:n[k] for k in ('node_id','parent_id','name','position','enabled','aliases','rule')} for n in nodes.values()]
    rid = insert(c,'sp_taxonomy_revisions',dict(prior_id=prior or None,reason=reason,actor=actor,created_at=now(),content_hash=sha(sorted(clean,key=lambda x:x['node_id']))))
    for n in sorted(nodes.values(),key=lambda x:len(ancestry(nodes,x['node_id']))):
        insert(c,'sp_taxonomy_nodes',dict(revision_id=rid,node_id=n['node_id'],parent_id=n['parent_id'],name=n['name'],position=n['position'],enabled=int(n['enabled']),aliases_json=js(n['aliases']),rule_json=js(n['rule']),rule_hash=rule_hash(nodes,n['node_id'])))
    return rid


def manage(db, action, expected_revision, actor, reason, node_id=None, values=None, cascade=False):
    if type(expected_revision) is not int or expected_revision < 0 or not actor.strip() or not reason.strip():
        raise ValueError('必须提供expected_revision、actor、reason')
    if type(cascade) is not bool:
        raise ValueError('cascade须为布尔值')
    if action not in ('initialize','add','update','move','disable','restore'):
        raise ValueError('未知目录操作')
    with connect(db,True) as c:
        cur = revision(c)
        if cur != expected_revision:
            raise ValueError(f'目录版本冲突：当前{cur}，提交{expected_revision}')
        if action == 'initialize':
            if cur:
                return {'revision':cur,'idempotent':True}
            for stmt in Path(__file__).with_name('schema.sql').read_text(encoding='utf-8').split(';'):
                if stmt.strip():
                    c.execute(stmt)
            nodes = {r['id']:dict(node_id=r['id'],parent_id=None,name=r['name'],position=i,enabled=True,aliases=[],rule={'include':r['includes'],'exclude':r['excludes'],'terms':[]}) for i,r in enumerate(frozen_roots())}
            for n in json.loads(SEED.read_text(encoding='utf-8')):
                nodes[n['node_id']] = n
            new = save(c,nodes,cur,reason,actor)
            return {'revision':new,'node_count':len(nodes),'created':True}
        _, nodes = load(c)
        old = copy.deepcopy(nodes)
        values = values or {}
        allowed = {'parent_id','name','position','aliases','rule'}
        if not isinstance(values,dict) or set(values)-allowed:
            raise ValueError('values仅允许parent_id/name/position/aliases/rule')
        if action == 'add':
            if not all(x in values for x in ('parent_id','name','rule')):
                raise ValueError('新增节点须提供父节点、名称和规则')
            if node_id is not None:
                raise ValueError('新节点ID由程序生成')
            node_id = 'N' + uuid.uuid4().hex
            nodes[node_id] = dict(node_id=node_id,parent_id=None,name='',position=100,enabled=True,aliases=[],rule={})
            nodes[node_id].update(values)
        else:
            if node_id not in nodes or nodes[node_id]['parent_id'] is None:
                raise ValueError('仅可维护已有二三级节点')
            if action in ('update','move'):
                if action=='move' and set(values)!={'parent_id'}:
                    raise ValueError('move仅接受parent_id')
                nodes[node_id].update(values)
            else:
                if values:
                    raise ValueError('停用/恢复不接受values')
                branch = descendants(nodes,node_id)
                if action=='disable' and len(branch)>1 and not cascade:
                    raise ValueError('有子节点；须显式cascade=true停用整支')
                for x in branch if cascade else {node_id}:
                    nodes[x]['enabled'] = action=='restore'
        new = save(c,nodes,cur,reason,actor)
        impacted = [nid for nid in old if rule_hash(old,nid) != rule_hash(nodes,nid)]
        return {'revision':new,'node_id':node_id,'reassessment_nodes':impacted,'reason':reason}


def query(db, search=None, revision_id=None, include_disabled=False):
    with connect(db) as c:
        rev,nodes = load(c,revision_id)
        rows = []
        for nid,n in nodes.items():
            path = ancestry(nodes,nid)
            if not include_disabled and not active(nodes,nid):
                continue
            if search and search.casefold() not in ' '.join([n['name'],*n['aliases']]).casefold():
                continue
            rows.append({**n,'depth':len(path),'path_ids':path,'path':[nodes[x]['name'] for x in path],'active':active(nodes,nid)})
        return {'revision':rev,'nodes':rows,'count':len(rows),'fixed_root_count':30}
