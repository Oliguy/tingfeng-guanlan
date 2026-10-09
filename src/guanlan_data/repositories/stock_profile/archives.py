"""Resolve changing file locations through the existing MinerU registry, without copying files."""
from guanlan_data.repositories.stock_profile.store import *

def resolve(c,legacy_id,original,parsed):
 if not legacy_id or not c.execute("SELECT 1 FROM sqlite_master WHERE name='mineru_archives'").fetchone():return original,parsed
 r=c.execute('SELECT * FROM mineru_archives WHERE document_id=?',(legacy_id,)).fetchone()
 if not r:return original,parsed
 if original and original['sha256']!=r['original_sha256']:raise ValueError('archive original identity conflict')
 original={'sha256':r['original_sha256'],'kind':'pdf','path':r['original_path'],'byte_size':None,'availability':'removed_after_archive' if r['pdf_removed_at'] else 'legacy_registered_not_rehashed'}
 parsed={'sha256':r['md_sha256'],'kind':'markdown','path':r['md_path'],'byte_size':None,'availability':'archive_registered_not_rehashed'}
 return original,parsed

def reconcile_locations(db):
 # Location/availability metadata may change; business and document versions do not.
 with connect(db,True) as c:
  if not c.execute("SELECT 1 FROM sqlite_master WHERE name='mineru_archives'").fetchone():return {'corrected_locations':0}
  rows=c.execute('SELECT a.id,a.path,m.original_path,m.pdf_removed_at FROM sp_artifacts a JOIN mineru_archives m ON a.sha256=m.original_sha256').fetchall();n=0
  for r in rows:
   availability='removed_after_archive' if r['pdf_removed_at'] else 'legacy_registered_not_rehashed'
   c.execute('UPDATE sp_artifacts SET path=?,availability=? WHERE id=?',(r['original_path'],availability,r['id']));n+=r['path']!=r['original_path']
 return {'corrected_locations':n,'registered_originals_checked':len(rows),'originals_copied':0}
