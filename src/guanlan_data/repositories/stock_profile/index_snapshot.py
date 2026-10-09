"""Public, read-only projection of current first-level industry memberships.

Uses the same revision/document relations as stock.profile.query. It does not
read report files, infer a classification, or mix in taxonomy focus memberships.
"""
import re
from guanlan_data.repositories.stock_profile.store import connect; from guanlan_data.repositories.stock_profile.store import js; from guanlan_data.repositories.stock_profile.store import sha; from guanlan_data.repositories.stock_profile.store import now; from guanlan_data.repositories.stock_profile.store import period

VERSION = 'stock.profile.index_snapshot.v1'
RULE_VERSION = 'trading_industries_30_v1'


def snapshot(db, report_period=None, codes=None):
    target = period(report_period) if report_period else None
    if codes is not None:
        if not isinstance(codes,(list,tuple,set)) or any(not isinstance(code,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',code) for code in codes):raise ValueError('INVALID_SECURITY_SCOPE')
        codes=sorted(set(codes))
    with connect(db) as c:
        c.execute('PRAGMA query_only=ON')
        rules = [dict(r) for r in c.execute(
            'SELECT * FROM sp_rules WHERE version=? ORDER BY industry', (RULE_VERSION,))]
        if [r['industry'] for r in rules] != [f'I{i:02}' for i in range(1, 31)]:
            raise ValueError('INDUSTRY_RULES_INCOMPLETE')
        # Latest revision is authoritative; a derived head must not hide a newer one.
        scope = '' if codes is None else ' WHERE company_id IN (SELECT company_id FROM sp_securities WHERE code IN ('+(','.join('?' for _ in codes) or 'NULL')+'))'
        sql = '''WITH latest AS MATERIALIZED (
            SELECT r.id,r.company_id,r.number,r.recorded_at,r.security_json FROM sp_revisions r JOIN
              (SELECT company_id,MAX(number) AS number FROM sp_revisions'''+scope+''' GROUP BY company_id) h
              ON r.company_id=h.company_id AND r.number=h.number),
            latest_period AS MATERIALIZED (
              SELECT rd.revision_id,MAX(d.period_end) AS period_end
              FROM sp_revision_documents rd JOIN sp_documents d ON d.id=rd.document_id
              JOIN latest r ON r.id=rd.revision_id GROUP BY rd.revision_id)
            SELECT r.id AS revision_id,r.number AS revision,r.recorded_at,
            r.security_json,cl.id AS classification_id,cl.industry,cl.status,
            cl.reason,cl.created_at AS classified_at,cl.effective_at,
            f.id AS fact_id,f.status AS fact_status,f.stage AS fact_stage,
            d.id AS document_id,d.period_end AS report_period,d.published_at
            FROM latest r
            JOIN latest_period lp ON lp.revision_id=r.id
            JOIN sp_revision_classifications rc ON rc.revision_id=r.id
            JOIN sp_classifications cl ON cl.id=rc.classification_id
            JOIN sp_facts f ON f.id=cl.fact_id
            JOIN sp_revision_documents rd ON rd.revision_id=r.id AND rd.document_id=f.document_id
            JOIN sp_documents d ON d.id=f.document_id
            WHERE cl.rule_version=? AND cl.relation_kind='actual'
            AND cl.status IN ('source_supported','verified')
            AND (d.period_end=COALESCE(?,lp.period_end) OR
              (d.period_end IS NULL AND d.kind IN ('announcement','interaction')))
            '''
        args=[*(codes or []),RULE_VERSION,target]
        sql+=' ORDER BY cl.industry,r.company_id,cl.id'
        raw = [dict(r) for r in c.execute(sql,args)]
        total = c.execute('SELECT COUNT(DISTINCT company_id) FROM sp_revisions').fetchone()[0]
        revision = c.execute('SELECT MAX(id) FROM sp_revisions').fetchone()[0]
    import json
    groups = {r['industry']: {'id': r['industry'], 'name': r['name'], 'members': {}} for r in rules}
    rejected = []
    for row in raw:
        securities = json.loads(row.pop('security_json'))
        for security in securities:
            code = security['code']
            if codes is not None and code not in codes:continue
            if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', code):
                rejected.append({'code': code, 'reason': 'NOT_CANONICAL_A_SHARE'}); continue
            # A classification of an actual relation cannot promote an unclear fact.
            if row['fact_stage'] != 'actual' or row['fact_status'] not in ('source_supported', 'verified'):
                rejected.append({'code': code, 'classification_id': row['classification_id'],
                                 'reason': 'FACT_NOT_SUPPORTED_ACTUAL'}); continue
            member = groups[row['industry']]['members'].setdefault(code, {
                'code': code, 'name': security['name'], 'relations': []})
            member['relations'].append(dict(row))
    for group in groups.values():
        group['members'] = [group['members'][s] for s in sorted(group['members'])]
        for member in group['members']:
            member['status'] = ('verified' if any(r['status'] == r['fact_status'] == 'verified'
                                                 for r in member['relations']) else 'source_supported')
    result = {'schema_version': VERSION, 'rule_version': RULE_VERSION, 'rules': rules,
              'scope': 'current_first_level_actual', 'report_period_requested': target,
              'classification_quality': 'existing_source_supported_and_verified_not_complete_market',
              'profile_companies': total, 'latest_revision_id': revision,
              'groups': list(groups.values()), 'rejected': rejected}
    if codes is not None:result['security_scope']=codes
    from guanlan_data.repositories.stock_profile.working_index_snapshot import merge
    result = merge(result, db, codes)
    result['snapshot_id'] = sha(result)
    result['read_at'] = now()
    return result
