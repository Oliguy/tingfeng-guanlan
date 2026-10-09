"""Explicit, revision-checked theme metadata commands; read paths never initialize DBs."""
from guanlan_data import sqlite as database
from contextlib import contextmanager
from datetime import datetime,timezone
import copy,json,re,sqlite3,uuid
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import canonical; from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_data.repositories.industry_index.store import writer_lock
from guanlan_data.repositories.observer_collections.config import catalog_path; from guanlan_data.repositories.observer_collections.config import input_paths

SCHEMA='observer.catalog.v1'
def now():return datetime.now(timezone.utc).isoformat()

@contextmanager
def writing(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with writer_lock(path):
        c=database.connect(path,timeout=5);c.row_factory=sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL')
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and 'themes' not in tables:raise ValueError('不是题材元数据库')
            c.executescript('''CREATE TABLE IF NOT EXISTS themes(id TEXT PRIMARY KEY,name TEXT NOT NULL,revision INTEGER NOT NULL,archived INTEGER NOT NULL,updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS theme_revisions(id TEXT NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,checksum TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(id,revision));
              CREATE TABLE IF NOT EXISTS theme_commands(request_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,result_json TEXT NOT NULL);''')
            c.execute('BEGIN IMMEDIATE');yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()

def list_themes(path=None,include_archived=False):
    path=Path(path or catalog_path())
    if not path.exists():return []
    with readonly(path) as c:
        return [dict(r) for r in c.execute('SELECT * FROM themes '+('' if include_archived else 'WHERE archived=0 ')+'ORDER BY updated_at DESC,id')]

def get(theme_id,path=None,revision=None):
    path=Path(path or catalog_path())
    if not path.exists():raise ValueError('题材不存在')
    with readonly(path) as c:
        head=c.execute('SELECT * FROM themes WHERE id=?',(theme_id,)).fetchone()
        if not head:raise ValueError('题材不存在或已移出目录')
        row=c.execute('SELECT payload_json,checksum FROM theme_revisions WHERE id=? AND revision=?',(theme_id,revision or head['revision'])).fetchone()
        if not row:raise ValueError('题材修订不存在')
        payload=json.loads(row['payload_json'])
        if digest(payload)!=row['checksum']:raise ValueError('题材内容校验失败')
        return {**payload,'head_revision':head['revision'],'head_archived':bool(head['archived'])}

def label(value,what):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>100 or any(ord(ch)<32 for ch in value):raise ValueError(what+'须为1–100字且无控制字符')
    return value.strip()

def normalize(theme_id,payload,market_path=None):
    if not isinstance(payload,dict) or set(payload)-{'name','members','source'}:raise ValueError('题材仅接受名称、成员及来源')
    name=label(payload.get('name'),'题材名称');members=payload.get('members',[])
    if not isinstance(members,list) or len(members)>5000:raise ValueError('题材成员应为最多5000条')
    source=payload.get('source',{})
    if not isinstance(source,dict) or set(source)-{'title','sha256','file','note'} or len(canonical(source))>3000:raise ValueError('来源信息无效')
    if any(not isinstance(v,str) for v in source.values()):raise ValueError('来源信息须为文本')
    merged={};tags={};issues=[];duplicates=0
    with readonly(market_path or input_paths()[1]) as c:
        for index,entry in enumerate(members):
            if not isinstance(entry,dict) or set(entry)-{'code','paths'}:raise ValueError('成员仅接受code与paths；不能传入其他题材的标签ID或股价')
            code=entry.get('code');paths=entry.get('paths',[])
            if not isinstance(code,str) or len(code)>100:raise ValueError('成员代码/名称无效')
            query='ts_code' if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',code.strip().upper()) else 'name'
            value=code.strip().upper() if query=='ts_code' else code.strip()
            rows=c.execute('SELECT ts_code,name FROM equity_master WHERE '+query+'=?',(value,)).fetchall()
            if len(rows)!=1:issues.append({'row':index+1,'input':code,'reason':'未找到证券' if not rows else '证券名称不唯一，请填市场代码'});continue
            code,name_=rows[0]
            if not isinstance(paths,list) or len(paths)>32:raise ValueError('每股标签路径最多32条')
            if code in merged:duplicates+=1
            member=merged.setdefault(code,{'code':code,'name':name_,'paths':[],'tag_ids':[],'order':len(merged),'status':'user_supplied','relations':[]})
            for path in paths:
                if not isinstance(path,list) or not 1<=len(path)<=4:raise ValueError('标签路径为1–4级文本数组')
                path=[label(v,'标签') for v in path]
                if path in member['paths']:continue
                parent=None
                for depth,part in enumerate(path):
                    tid='L'+digest([theme_id,path[:depth+1]])[:24]
                    tags.setdefault(tid,{'id':tid,'theme_id':theme_id,'name':part,'parent_id':parent,'path':path[:depth+1],'order':len(tags)})
                    parent=tid
                member['paths'].append(path);member['tag_ids'].append(parent)
    return {'name':name,'members':list(merged.values()),'tags':list(tags.values()),'source':source},issues,duplicates

def preview(payload,theme_id=None,expected_revision=0,path=None,market_path=None):
    if type(expected_revision)is not int or expected_revision<0:raise ValueError('修订号无效')
    theme_id=theme_id or 'T'+uuid.uuid4().hex
    if not re.fullmatch(r'T[0-9a-f]{32}',theme_id):raise ValueError('题材ID无效')
    old=get(theme_id,path) if expected_revision else None
    if old and old['head_revision']!=expected_revision:raise ValueError('题材已被其他窗口修改，请重新读取')
    clean,issues,duplicates=normalize(theme_id,payload,market_path)
    before={m['code'] for m in old['members']} if old else set();after={m['code'] for m in clean['members']}
    result={'id':theme_id,'expected_revision':expected_revision,'payload':clean,'issues':issues,'duplicate_rows_merged':duplicates,
            'added':sorted(after-before),'removed':sorted(before-after),'member_count':len(after),'tag_relations':sum(len(m['paths']) for m in clean['members'])}
    return {**result,'preview_hash':digest(result)}

def editable(payload):
    return {'name':payload['name'],'members':[{'code':m['code'],'paths':m['paths']} for m in payload['members']],'source':payload.get('source',{})}

def commit(draft,request_id,path=None,market_path=None):
    if not isinstance(draft,dict):raise ValueError('缺少题材预览')
    supplied=draft.get('preview_hash');base={k:v for k,v in draft.items() if k!='preview_hash'}
    if supplied!=digest(base) or draft.get('issues'):raise ValueError('预览有未解决问题或内容已变化')
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('请求编号无效')
    clean,issues,_=normalize(draft['id'],editable(draft['payload']),market_path)
    # Merging repeated rows can reorder first-seen tags. Preserve the reviewed
    # display order while revalidating every tag's scoped identity and ancestry.
    original=draft['payload']
    tag_fields=lambda tags:{t['id']:{k:v for k,v in t.items() if k!='order'} for t in tags}
    if (issues or {k:v for k,v in clean.items() if k!='tags'}!={k:v for k,v in original.items() if k!='tags'}
        or tag_fields(clean['tags'])!=tag_fields(original['tags'])
        or [t['order'] for t in original['tags']]!=list(range(len(original['tags'])))):
        raise ValueError('证券身份或标签已变化，请重新预览')
    clean['tags']=copy.deepcopy(original['tags'])
    reqhash=digest(['commit',draft]);path=Path(path or catalog_path())
    with writing(path) as c:
        cached=c.execute('SELECT * FROM theme_commands WHERE request_id=?',(request_id,)).fetchone()
        if cached:
            if cached['request_hash']!=reqhash:raise ValueError('请求编号已用于其他修改')
            return json.loads(cached['result_json'])
        head=c.execute('SELECT revision,archived FROM themes WHERE id=?',(draft['id'],)).fetchone()
        if (head['revision'] if head else 0)!=draft['expected_revision']:raise ValueError('题材版本冲突，请重新预览')
        if head and head['archived']:raise ValueError('请先恢复已归档题材')
        revision=draft['expected_revision']+1
        payload={'id':draft['id'],'revision':revision,'archived':False,**clean}
        c.execute('INSERT INTO theme_revisions VALUES(?,?,?,?,?)',(draft['id'],revision,canonical(payload),digest(payload),now()))
        c.execute('INSERT INTO themes VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=excluded.revision,archived=0,updated_at=excluded.updated_at',(draft['id'],clean['name'],revision,0,now()))
        result={'id':draft['id'],'revision':revision,'name':clean['name'],'member_count':len(clean['members']),'status':'saved_pending_build'}
        c.execute('INSERT INTO theme_commands VALUES(?,?,?)',(request_id,reqhash,canonical(result)))
        return result

def archive(theme_id,expected_revision,request_id,*,restore=False,path=None):
    if not isinstance(request_id,str) or not 1<=len(request_id)<=128 or type(expected_revision)is not int:raise ValueError('请求或修订号无效')
    path=Path(path or catalog_path());reqhash=digest([theme_id,expected_revision,restore])
    with writing(path) as c:
        old=c.execute('SELECT * FROM theme_commands WHERE request_id=?',(request_id,)).fetchone()
        if old:
            if old['request_hash']!=reqhash:raise ValueError('请求编号已用于其他操作')
            return json.loads(old['result_json'])
        head=c.execute('SELECT * FROM themes WHERE id=?',(theme_id,)).fetchone()
        if not head or head['revision']!=expected_revision:raise ValueError('题材版本冲突')
        if bool(head['archived'])==(not restore):raise ValueError('题材已经处于目标状态')
        current=json.loads(c.execute('SELECT payload_json FROM theme_revisions WHERE id=? AND revision=?',(theme_id,expected_revision)).fetchone()[0])
        current.update(revision=expected_revision+1,archived=not restore)
        c.execute('INSERT INTO theme_revisions VALUES(?,?,?,?,?)',(theme_id,current['revision'],canonical(current),digest(current),now()))
        c.execute('UPDATE themes SET revision=?,archived=?,updated_at=? WHERE id=?',(current['revision'],int(not restore),now(),theme_id))
        result={'id':theme_id,'revision':current['revision'],'archived':not restore,'status':'saved_pending_build' if restore else 'archived'}
        c.execute('INSERT INTO theme_commands VALUES(?,?,?)',(request_id,reqhash,canonical(result)))
        return result

def industry_collections(business_path,codes=None):
    """Use public catalog/association projections. No keyword classification or writes."""
    from guanlan_data.repositories.stock_profile.index_snapshot import snapshot
    from guanlan_data.repositories.industry_taxonomy.catalog import query as nodes_query
    from guanlan_data.repositories.industry_taxonomy.service import query as memberships_query
    import guanlan_data.repositories.industry_taxonomy.working_snapshot as working_snapshot
    first=snapshot(business_path,codes=codes);directory=nodes_query(business_path)
    working=working_snapshot.query(business_path,directory)
    if working:first={**first,'focus_snapshot':working['snapshot_id']}
    if codes is None:records=memberships_query(business_path,stage='actual')
    else:
        with readonly(business_path) as c:
            known=[r[0] for r in c.execute('SELECT code FROM sp_securities WHERE code IN ('+(','.join('?' for _ in codes) or 'NULL')+')',list(codes))]
        records={'items':[]}
        for start in range(0,len(known),200):records['items'].extend(memberships_query(business_path,stage='actual',codes=known[start:start+200])['items'])
    groups=[{**g,'kind':'industry','parent_id':None,'revision':first['snapshot_id'],'quality':g.get('quality','existing_classification')} for g in first['groups']]
    for node in directory['nodes']:
        if not node['parent_id']:continue
        members=[]
        for item in records['items']:
            ms=[m for m in item['memberships'] if node['node_id'] in m['path_ids'] and m['freshness']=='current']
            if ms:members.append({'code':item['code'],'name':item['name'],'status':'source_supported','relations':[],
                                 'paths':list(dict.fromkeys(tuple(m['path'][1:]) for m in ms)),
                                 'original_industries':item['original_industries']})
        covered=working and node['node_id'] in working['nodes']
        source={}
        if covered:
            merged={m['code']:m for m in working_snapshot.members(working,node,directory)}
            for member in members:
                prior=merged.get(member['code'])
                if prior:
                    member={**member,'paths':list(dict.fromkeys(tuple(p) for p in member['paths']+prior['paths'])),
                            'relations':member['relations']+prior['relations']}
                merged[member['code']]=member
            members=[merged[code] for code in sorted(merged)]
            source={'title':working['source_title'],'sha256':working['snapshot_id'],
                    'note':'固定工作分类；仅实际经营阶段参与指数，保留待定与未核实标记。行情更新不改变归属。'}
        if codes is not None:members=[m for m in members if m['code'] in codes]
        groups.append({'id':node['node_id'],'name':node['name'],'parent_id':node['parent_id'],'kind':'subindustry','path':node['path'],
                       'members':members,'revision':digest([directory['revision'],node,members,source]),
                       'quality':'working_unverified' if covered else 'classified_sample',**({'source':source} if source else {})})
    return groups,first
