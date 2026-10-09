"""Explicit import of an already reviewed structured draft; no OCR/model runtime."""
import hashlib,json,shutil
from pathlib import Path
import guanlan_data.repositories.observer_collections.catalog as catalog
from guanlan_data.repositories.observer_collections.config import catalog_path

def import_draft(draft_path,path=None,market_path=None):
    draft=json.loads(Path(draft_path).read_text('utf-8'))
    source=Path(draft['source_file']);sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if sha!=draft['source_sha256']:raise ValueError('图片来源已变化，请重新核对')
    if any(not r.get('resolved_code') for r in draft['rows']):raise ValueError('仍有未解析证券')
    path=Path(path or catalog_path());tid='T'+sha[:32];rid='image-import:'+sha
    sources=path.parent/'sources';sources.mkdir(parents=True,exist_ok=True)
    archived=sources/(sha+source.suffix.lower())
    if archived.exists():
        if hashlib.sha256(archived.read_bytes()).hexdigest()!=sha:raise ValueError('已归档来源校验失败')
    else:shutil.copyfile(source,archived)
    payload={'name':draft['theme_name'],'members':[{'code':r['resolved_code'],'paths':[r['tag_path']]} for r in draft['rows']],
             'source':{'title':draft['source_title'],'sha256':sha,'file':str(archived),'note':'用户提供的题材关联；未导入图中财务数字及红字评级。'}}
    proposed=catalog.preview(payload,tid,0,path,market_path)
    if proposed['member_count']!=draft['unique_resolved_codes'] or proposed['tag_relations']!=draft['row_count']:raise ValueError('导入草稿成员/标签数量不一致')
    return {'preview':proposed,'receipt':catalog.commit(proposed,rid,path,market_path)}
