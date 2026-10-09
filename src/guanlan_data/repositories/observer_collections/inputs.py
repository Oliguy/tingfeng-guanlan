"""Offline reuse of verified support responses across different collection batches."""
import json
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly; from guanlan_data.repositories.industry_index.inputs import digest

class ReadOnlySupport:
    def __init__(self,path,provider=None,online=False):
        if online:raise ValueError('ReadOnlySupport cannot fetch')
        self.receipts={};self.network_calls=0;self.records=[]
        if Path(path).exists():
            with readonly(path) as c:
                self.records=[dict(r) for r in c.execute('SELECT * FROM responses ORDER BY fetched_at,request_id')]

    def request(self,endpoint,**params):
        codes=set(params.get('ts_code','').split(','));chosen={}
        for record in self.records:
            if record['endpoint']!=endpoint:continue
            original=json.loads(record['params'])
            if not codes.intersection(original.get('ts_code','').split(',')):continue
            rows=json.loads(record['rows'])
            if digest(rows)!=record['checksum']:raise ValueError('SUPPORT_CACHE_CORRUPT')
            used=False
            for row in rows:
                if row.get('ts_code') not in codes:continue
                day=row.get('trade_date')
                if day and not params['start_date']<=day<=params['end_date']:continue
                key=(row['ts_code'],day,row.get('suspend_type'),row.get('suspend_timing'))
                chosen[key]=row;used=True
            if used:self.receipts[record['request_id']]=record['checksum']
        # Lack of cached evidence is never converted to a suspension. The index
        # calculator will reject unresolved gaps for the affected collection.
        return list(chosen.values())
