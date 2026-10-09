"""Bounded public product projection from existing profiles; no ingestion or writes."""
import json
from pathlib import Path
from guanlan_data.repositories.stock_profile.store import connect


def summaries(db, codes, *, fact_ids=None):
    """Read referenced industry facts, or current actual businesses for other stocks.

    Explicit fact bindings never fall back to a newer company revision. Product
    names and review status are kept as stored; this does not rank revenues.
    """
    codes = list(dict.fromkeys(codes))
    if len(codes) > 5000:
        raise ValueError('产品查询最多5000只股票')
    fact_ids = fact_ids or {}
    result = {code: {'products': [], 'periods': [], 'statuses': [],
                     'basis': 'collection_facts' if code in fact_ids else 'current_profile'} for code in codes}
    if not codes or not Path(db).is_file():
        return result

    def append(code, row):
        try:
            names = json.loads(row['products_json'])
        except (ValueError, TypeError):
            names = []
        if not isinstance(names, list):
            names = []
        names = [p.strip() for p in names if isinstance(p, str) and p.strip()]
        if not names and row['original_name']:
            names = [row['original_name']]
        entry = result[code]
        for value in names:
            if value not in entry['products']:
                entry['products'].append(value)
        for field, value in (('periods', row['period_end']), ('statuses', row['status'])):
            if value and value not in entry[field]:
                entry[field].append(value)

    with connect(db) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'sp_securities', 'sp_documents', 'sp_facts'} <= tables:
            return result
        ids = sorted({fid for code in codes for fid in fact_ids.get(code, []) if type(fid) is int})
        for start in range(0, len(ids), 200):
            part = ids[start:start + 200]
            rows = c.execute('''SELECT s.code,f.id,f.original_name,f.products_json,f.status,d.period_end
                FROM sp_facts f JOIN sp_documents d ON d.id=f.document_id
                JOIN sp_securities s ON s.company_id=d.company_id
                WHERE f.stage='actual' AND f.id IN (''' + ','.join('?' for _ in part) + ') ORDER BY f.id', part)
            for row in rows:
                if row['code'] in result and row['id'] in fact_ids.get(row['code'], []):
                    append(row['code'], row)
        if not {'sp_heads', 'sp_revision_documents'} <= tables:
            return result
        remaining = [code for code in codes if code not in fact_ids]
        for start in range(0, len(remaining), 200):
            part = remaining[start:start + 200]
            rows = c.execute('''SELECT s.code,f.original_name,f.products_json,f.status,d.period_end
                FROM sp_securities s JOIN sp_heads h ON h.company_id=s.company_id
                JOIN sp_revision_documents rd ON rd.revision_id=h.revision_id
                JOIN sp_documents d ON d.id=rd.document_id JOIN sp_facts f ON f.document_id=d.id
                WHERE s.code IN (''' + ','.join('?' for _ in part) + ''') AND f.stage='actual'
                AND (d.period_end=h.period_end OR (d.period_end IS NULL AND d.kind IN ('announcement','interaction')))
                ORDER BY s.code,d.period_end DESC,f.id''', part)
            for row in rows:
                append(row['code'], row)
        # Some provider profiles only have product revenue disclosures, with no
        # actual-stage business facts. Read those product names as disclosures;
        # never promote their facts or change industry membership/stage.
        if 'sp_contexts' in tables:
            missing = [code for code in remaining if not result[code]['products']]
            for start in range(0, len(missing), 200):
                part = missing[start:start + 200]
                rows = c.execute('''SELECT s.code,cx.label AS original_name,'[]' AS products_json,
                    cx.status,cx.period_end FROM sp_securities s JOIN sp_heads h ON h.company_id=s.company_id
                    JOIN sp_revision_documents rd ON rd.revision_id=h.revision_id
                    JOIN sp_contexts cx ON cx.document_id=rd.document_id
                    WHERE s.code IN (''' + ','.join('?' for _ in part) + ''') AND cx.dimension='product'
                    AND cx.period_end=h.period_end AND cx.scope='consolidated' ORDER BY s.code,cx.id''', part)
                for row in rows:
                    result[row['code']]['basis'] = 'current_product_disclosure'
                    append(row['code'], row)
    return result
