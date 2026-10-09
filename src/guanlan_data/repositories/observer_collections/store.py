"""Immutable collection publications, atomic per-object pointers and shared references."""
from guanlan_data import sqlite as database
from contextlib import contextmanager
import json,sqlite3
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import canonical; from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_data.repositories.industry_index.store import writer_lock
from guanlan_data.repositories.observer_collections.catalog import now

@contextmanager
def connection(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    c=database.connect(path,timeout=10);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and 'publications' not in tables:raise ValueError('不是观察集合结果库')
        c.executescript('''CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY,collection_id TEXT NOT NULL,kind TEXT NOT NULL,parent_id TEXT,revision TEXT NOT NULL,header_json TEXT NOT NULL,group_json TEXT NOT NULL,summary_json TEXT NOT NULL,checksum TEXT NOT NULL,created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS current_publications(collection_id TEXT PRIMARY KEY,publication_id TEXT NOT NULL REFERENCES publications(id));
          CREATE TABLE IF NOT EXISTS quote_refs(id TEXT PRIMARY KEY,code TEXT NOT NULL,ref_json TEXT NOT NULL,summary_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS publication_members(publication_id TEXT NOT NULL REFERENCES publications(id),code TEXT NOT NULL,ref_id TEXT NOT NULL REFERENCES quote_refs(id),PRIMARY KEY(publication_id,code));
          CREATE TABLE IF NOT EXISTS failures(collection_id TEXT PRIMARY KEY,revision TEXT,message TEXT,updated_at TEXT);
          CREATE INDEX IF NOT EXISTS publication_by_collection ON publications(collection_id,created_at);''')
        yield c
    finally:c.close()

def publish(path,collection,group,header,refs,*,catalog_path=None,before_commit=None):
    if collection['kind']=='theme' and catalog_path is None:raise ValueError('题材发布须绑定目录库')
    from contextlib import nullcontext
    guard=writer_lock(catalog_path) if catalog_path else nullcontext()
    with guard:
        if catalog_path:
            from guanlan_data.repositories.observer_collections.catalog import get
            current=get(collection['id'],catalog_path)
            if current['head_revision']!=collection['revision'] or current['head_archived']:raise ValueError('目录已变化，保留上一发布；请应用最新修订')
        member_refs={m['code']:refs[m['code']] for m in group['members']}
        content=digest([collection,header,group,{code:r['id'] for code,r in member_refs.items()}]);publication_id=content
        # Summary contains precomputed weekly signal, not every daily/member row.
        from guanlan_domain.observer_math.signals import strength
        summary={'id':collection['id'],'name':collection['name'],'kind':collection['kind'],'parent_id':collection.get('parent_id'),
                 'revision':collection['revision'],'quality':collection['quality'],'path':collection.get('path'),
                 'publication_id':publication_id,'date':header['actual_end'],'stats':group['stats'],
                 'latest':group['points'][-1] if group['points'] else {},'signal':strength(group['weekly_points']),
                 'member_count':len(group['members'])}
        with connection(path) as c:
            try:
                c.execute('BEGIN IMMEDIATE')
                old=c.execute('SELECT checksum FROM publications WHERE id=?',(publication_id,)).fetchone()
                if old and old[0]!=content:raise ValueError('发布内容冲突')
                if not old:
                    c.execute('INSERT INTO publications VALUES(?,?,?,?,?,?,?,?,?,?)',(publication_id,collection['id'],collection['kind'],collection.get('parent_id'),str(collection['revision']),canonical(header),canonical(group),canonical(summary),content,now()))
                    for code,r in member_refs.items():
                        c.execute('INSERT OR IGNORE INTO quote_refs VALUES(?,?,?,?)',(r['id'],code,canonical(r['ref']),canonical(r['summary'])))
                        c.execute('INSERT INTO publication_members VALUES(?,?,?)',(publication_id,code,r['id']))
                c.execute('INSERT INTO current_publications VALUES(?,?) ON CONFLICT(collection_id) DO UPDATE SET publication_id=excluded.publication_id',(collection['id'],publication_id))
                c.execute('DELETE FROM failures WHERE collection_id=?',(collection['id'],))
                if before_commit:before_commit()
                c.commit()
            except BaseException:c.rollback();raise
    return {'id':collection['id'],'publication_id':publication_id,'revision':collection['revision'],'idempotent':bool(old),'member_count':len(member_refs)}

def failure(path,collection,message):
    with connection(path) as c:
        with c:c.execute('INSERT OR REPLACE INTO failures VALUES(?,?,?,?)',(collection['id'],str(collection['revision']),message,now()))

def failure_message(path,collection_id):
    """A detail request needs one failure, not every collection summary."""
    with readonly(path) as c:
        row=c.execute('SELECT message FROM failures WHERE collection_id=?',(collection_id,)).fetchone()
        return row[0] if row else None

def current_publication(path,collection_id):
    with readonly(path) as c:
        row=c.execute('SELECT publication_id FROM current_publications WHERE collection_id=?',(collection_id,)).fetchone()
        return row[0] if row else None

def summaries(path,calendar=None,*,signal_reader=None):
    if not Path(path).exists():return [],{}
    with readonly(path) as c:
        rows=[json.loads(r[0]) for r in c.execute('SELECT p.summary_json FROM current_publications h JOIN publications p ON p.id=h.publication_id ORDER BY p.collection_id')]
        failures={r['collection_id']:dict(r) for r in c.execute('SELECT * FROM failures')}
        if signal_reader is not None:
            signals=signal_reader.group_signals(r['publication_id'] for r in rows)
            if signals is not None:
                from guanlan_data.repositories.observer_series.reader import unavailable
                for row in rows:row['signal']=signals.get(row['publication_id']) or unavailable('集合指标尚未生成，请本地重算')
                return rows,failures
        if calendar is not None or signal_reader is not None:
            from guanlan_domain.observer_math.signals import strength
            weekly={r[0]:json.loads(r[1]) for r in c.execute("SELECT p.id,json_extract(p.group_json,'$.weekly_points') FROM current_publications h JOIN publications p ON p.id=h.publication_id")}
            for row in rows:
                if signal_reader is not None:
                    from guanlan_data.repositories.observer_series.reader import unavailable
                    row['signal']=signal_reader.group_signal(row['publication_id'],weekly[row['publication_id']]) or unavailable('集合指标尚未生成或日历已变，请本地重算')
                else:row['signal']=strength(weekly[row['publication_id']],calendar)
        return rows,failures

def detail(path,collection_id,publication_id=None):
    if not Path(path).exists():raise ValueError('尚未发布此集合，请先本地重算')
    with readonly(path) as c:
        if not publication_id:
            row=c.execute('SELECT publication_id FROM current_publications WHERE collection_id=?',(collection_id,)).fetchone()
            publication_id=row[0] if row else None
            if not publication_id:
                failed=c.execute('SELECT message FROM failures WHERE collection_id=?',(collection_id,)).fetchone()
                raise ValueError('尚未发布，请从题材管理或更新面板本地应用'+('：'+failed[0] if failed else ''))
        row=c.execute('SELECT * FROM publications WHERE id=? AND collection_id=?',(publication_id,collection_id)).fetchone()
        if not row:raise ValueError('集合与发布版本不匹配，请刷新列表')
        quotes={r['code']:{'ref':json.loads(r['ref_json']),'summary':json.loads(r['summary_json'])} for r in c.execute('SELECT m.code,q.ref_json,q.summary_json FROM publication_members m JOIN quote_refs q ON q.id=m.ref_id WHERE m.publication_id=?',(publication_id,))}
        return {'publication_id':publication_id,'header':json.loads(row['header_json']),'group':json.loads(row['group_json']),'summary':json.loads(row['summary_json']),'quotes':quotes}

def member_detail(path,collection_id,publication_id):
    """Pinned member identities and references, without chart/report payloads."""
    with readonly(path) as c:
        row=c.execute('SELECT kind,header_json FROM publications WHERE id=? AND collection_id=?',(publication_id,collection_id)).fetchone()
        if not row:raise ValueError('集合与发布版本不匹配，请刷新列表')
        refs={r['code']:{'ref':json.loads(r['ref_json'])} for r in c.execute(
            'SELECT m.code,q.ref_json FROM publication_members m JOIN quote_refs q ON q.id=m.ref_id WHERE m.publication_id=?',(publication_id,))}
        return {'publication_id':publication_id,'header':json.loads(row['header_json']),
                'group':{'id':collection_id,'kind':row['kind'],'members':[{'code':code} for code in refs]},'quotes':refs}
