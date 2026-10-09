"""Explicit update orchestration. Reading never imports this module."""
from pathlib import Path
import hashlib
import time
from guanlan_data.repositories.stock_profile.index_snapshot import snapshot
from guanlan_data.repositories.industry_index.inputs import read_market; from guanlan_data.repositories.industry_index.inputs import digest
from guanlan_domain.industry_index.engine import calculate; from guanlan_domain.industry_index.engine import METHOD
from guanlan_data.repositories.industry_index.store import writer_lock; from guanlan_data.repositories.industry_index.store import publish
from guanlan_data.repositories.industry_index.support import repair_inputs; from guanlan_data.repositories.industry_index.support import valuation_quotes
from guanlan_domain.industry_index.indicators import enrich; from guanlan_domain.industry_index.indicators import provenance


def build(classification_db, market_db, output_db, *, end_date, start_date=None, online=True):
    output = Path(output_db).resolve()
    sources = [Path(p).resolve() for p in (classification_db, market_db)]
    if any(output == p or (output.exists() and output.samefile(p)) for p in sources):
        raise ValueError('OUTPUT_MUST_NOT_BE_INPUT')
    with writer_lock(output):
        started = time.perf_counter()
        membership = snapshot(classification_db)
        codes = sorted({m['code'] for g in membership['groups'] for m in g['members']})
        market = read_market(market_db, codes, end_date=end_date, start_date=start_date)
        repair_inputs(market, output.with_name('support.sqlite'), online=online)
        from guanlan_data.repositories.industry_index.member_prices import freeze
        member_prices = freeze(market)
        market['quotes'] = valuation_quotes(market)
        header = {'method_version': METHOD, 'classification_snapshot': membership['snapshot_id'],
                  'classification_read_at': membership['read_at'], 'market_hash': market['market_hash'],
                  'calendar_hash': market['calendar_hash'], 'calendar_source': market['calendar_source'],
                  'start_date': market['start_date'], 'requested_end': market['requested_end'],
                  'actual_end': market['actual_end'], 'market_vintage': market['market_vintage'],
                  'source_latest_at': market['source_latest_at'], 'unique_classified_securities': len(codes),
                  'profile_companies': membership['profile_companies'], 'classification_complete': False,
                  'classification_quality': membership['classification_quality'],
                  'input_paths': [str(p) for p in sources],
                  'units': {'price': '指数点', 'volume': '股', 'amount': '元',
                            'mean_volume': '股/只', 'relative_volume_20': '倍'},
                  'external_model_calls': 0}
        header.update(member_snapshot_version=1, display_dates=market['display_dates'], support_hash=market['support_hash'], support=market['support'],
                      etf_components=provenance(), calculation_start=market['calculation_dates'][0])
        import guanlan_data.repositories.stock_profile.index_snapshot as member_module
        source_files = [Path(__file__).with_name(name) for name in
                        ('engine.py', 'inputs.py', 'store.py', 'service.py', 'support.py', 'indicators.py', 'config.py', 'member_prices.py')]
        source_files.append(Path(member_module.__file__))
        header['implementation_hash'] = digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in source_files})
        # Reading time changes on every run, but identical inputs remain idempotent.
        header['run_id'] = digest({k: v for k, v in header.items() if k != 'classification_read_at'})
        groups = calculate(membership, market)
        if any(g['stats']['unknown_identity_members'] for g in groups): raise ValueError('UNRESOLVED_SECURITY_IDENTITY')
        gaps=[g['id']+':'+str(g['stats']['gap_sessions']) for g in groups if g['stats']['gap_sessions']]
        if gaps:raise ValueError('历史行情仍有未解释缺口，未发布；请补齐股票源后重算：'+', '.join(gaps))
        groups = [enrich(g) for g in groups]
        result = publish(output, header, groups, member_prices=member_prices)
        result.update(elapsed_seconds=time.perf_counter() - started,
                      as_of=header['actual_end'], requested_end=end_date,
                      input_rows=sum(len(rows) for rows in market['quotes'].values()),
                      coverage=[{'id': g['id'], 'name': g['name'], **g['stats']} for g in groups])
        return result
