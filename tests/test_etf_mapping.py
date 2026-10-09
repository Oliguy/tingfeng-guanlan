import pytest
from guanlan_domain.etf_mapping import classify
from guanlan_domain.etf_evidence import exposure_summary,index_identity
from guanlan_domain.etf_scope import ScopeRules

def mapping(profile,holdings=(),memberships=None):
    nodes={f'I{i:02}':{'node_id':f'I{i:02}','name':'行业'+str(i),'parent_id':None,'enabled':True,'aliases':[]} for i in range(1,31)}
    nodes['I01']['name']='光通信';nodes['I02']['name']='半导体';nodes['I25']['name']='银行'
    return classify(profile,holdings,nodes,memberships or {},exposure=exposure_summary,
        identity=index_identity,scope=ScopeRules.scope_dimensions,excluded=ScopeRules._exclusion_reason,catalog_revision=1)

def test_current_own_root_and_provenance():
    r=mapping({'tracking_index_name':'中证银行指数','tracking_index_code':'399986.CSI','profile_source_hash':'evidence'})
    assert r['group_id']=='I25' and r['taxonomy_node_id']=='I25'
    assert r['evidence']['tracking_index_identity']=='CSI:399986'
    assert r['evidence']['catalog_revision']==1

def test_generic_communication_is_not_optical():
    assert mapping({'tracking_index_name':'中证通信指数'})['state']=='unclassified'
    assert mapping({'tracking_index_name':'中证光通信指数'})['group_id']=='I01'

def test_partial_weights_are_lower_bound_and_missing_are_unknown():
    h=[{'stock_code':'600001','weight_pct':20},{'stock_code':'600002','weight_pct':None}]
    r=mapping({'tracking_index_name':'通信'},h,{'600001':['I01'],'600002':['I01']})
    assert r['state']=='unclassified'
    e=r['evidence']['holding_exposure']
    assert e['exposures'][0]['exposure_lower_bound_pct']==20 and e['unobserved_weight_pct']==80

def test_foreign_codes_and_namespace_collisions():
    h=[{'stock_code':'600001.HK','weight_pct':70}]
    assert mapping({'tracking_index_name':'通信'},h,{'600001':['I01']})['state']=='unclassified'
    assert index_identity({'tracking_index_code':'000001.CSI'})!=index_identity({'tracking_index_code':'000001.SH'})
    assert index_identity({'tracking_index_code':'000001'}) is None

@pytest.mark.parametrize('profile',[{'tracking_index_name':'沪深300'}, {'fund_name':'黄金ETF'}, {'tracking_index_name':'纳斯达克半导体'}])
def test_broad_foreign_commodity_not_forced(profile):
    assert mapping(profile)['state']!='classified'

def test_conflict_and_duplicate_weights_not_promoted():
    h=[{'stock_code':'600001','weight_pct':70}]
    assert mapping({'tracking_index_name':'半导体'},h,{'600001':['I25']})['reason']=='tracking_index_holdings_conflict'
    h.append({'stock_code':'600001','weight_pct':80})
    assert mapping({'tracking_index_name':'银行'},h,{'600001':['I25']})['reason']=='holding_weights_invalid'

def test_name_only_and_multi_sector_remain_pending():
    assert mapping({'fund_name':'银行ETF'})['state']=='unclassified'
    assert mapping({'tracking_index_name':'半导体银行组合'})['state']=='unclassified'
