"""Conservative mapping into Guanlan's own catalog; no database or network IO."""
import re

VERSION = 'guanlan_etf_trading30_v1'
# Explicit product/sector synonyms. Generic communications/technology/new energy
# span several roots and deliberately have no single-root shortcut.
TERMS = {
 'I01':['光通信','光模块','光纤','CPO'], 'I02':['半导体','芯片','集成电路'],
 'I03':['存储'], 'I04':['计算机设备','服务器','网络设备'],
 'I05':['消费电子','电子元件','电子元器件'], 'I06':['软件','云计算','云服务'],
 'I07':['数据中心','液冷'], 'I08':['机械','机器人','工业母机'],
 'I09':['汽车','智能驾驶'], 'I10':['航空航天','军工','国防','卫星'],
 'I11':['电网','电气设备'], 'I12':['核电'], 'I13':['光伏'], 'I14':['风电'],
 'I15':['电池','储能','锂电'], 'I16':['煤炭','油气','石油天然气'],
 'I17':['有色','稀土','黄金矿业'], 'I18':['化工','新材料'],
 'I19':['医药','创新药','生物药'], 'I20':['医疗器械','医疗服务'],
 'I21':['农业','农牧','养殖','农林牧渔'], 'I22':['食品','饮料','白酒'],
 'I23':['家电','日用消费'], 'I24':['商贸','旅游','传媒','游戏','文娱'],
 'I25':['银行'], 'I26':['证券','券商','保险','非银'],
 'I27':['公用事业','电力运营','绿色电力','水务'],
 'I28':['交通运输','物流','航运'], 'I29':['房地产','地产','建筑'],
 'I30':['钢铁','建材','水泥']}


def matches(text, terms):
    text = str(text or '').upper()
    return [t for t in terms if (bool(re.search(r'(?<![A-Z])'+re.escape(t.upper())+r'(?![A-Z])', text))
                               if t.isascii() else t.upper() in text)]


def classify(profile, holdings, nodes, memberships, *, exposure, identity, scope, excluded, catalog_revision):
    evidence = {'profile':dict(profile), 'tracking_index_identity':identity(profile),
                'catalog_revision':catalog_revision, 'scope':scope(profile),
                'classification_basis':'source_supported_or_verified_actual_only',
                'holding_exposure':exposure(holdings,memberships), 'lexical':[]}
    def pending(reason):
        return {'state':'unclassified','reason':reason,'confidence':0.,'method_version':VERSION,'evidence':evidence}
    text = ' '.join(str(profile.get(k) or '') for k in ('fund_name','fund_type','tracking_index_name','benchmark'))
    reason = excluded(profile,text)
    if reason:
        return {'state':'excluded','reason':reason,'confidence':1.,'method_version':VERSION,'evidence':evidence}
    if evidence['scope']['investment_market'] != 'mainland_candidate':
        return pending('not_confirmed_mainland_industry_scope')
    field = next((k for k in ('tracking_index_name','benchmark','fund_name') if profile.get(k)), None)
    needle = profile.get(field,'') if field else ''
    roots = {n['node_id']:n for n in nodes.values() if n['parent_id'] is None and n['enabled']}
    candidates = set()
    for nid,node in roots.items():
        words = [node['name'],*node.get('aliases',[]),*TERMS.get(nid,[])]
        hit = matches(needle,words)
        if hit:
            candidates.add(nid);evidence['lexical'].append({'node_id':nid,'field':field,'matched_terms':hit})
    # Exact subindustry scope can disambiguate overlapping root words; only
    # authoritative enabled descendants are accepted, never fuzzy string scores.
    sub = [n for n in nodes.values() if n['parent_id'] is not None and n['enabled']
           and matches(needle,[n['name'],*n.get('aliases',[]),*n.get('rule',{}).get('terms',[])])]
    parent_ids = set()
    for n in sub:
        parent=n
        while parent['parent_id'] is not None:
            parent=nodes[parent['parent_id']]
        if parent['node_id'] in roots:
            parent_ids.add(parent['node_id'])
    if len(parent_ids)==1:
        candidates=parent_ids
    observed=evidence['holding_exposure']
    if not observed['weight_valid']:
        return pending('holding_weights_invalid')
    ranked=observed['exposures']
    strong=ranked[0] if ranked and ranked[0]['exposure_lower_bound_pct']>=60 else None
    if strong and len(ranked)>1 and ranked[1]['exposure_lower_bound_pct']>=40:
        return pending('overlapping_industry_exposure')
    if len(candidates)>1:
        return pending('tracking_index_spans_multiple_industries')
    root=next(iter(candidates),None)
    if strong and root and strong['group_id']!=root:
        return pending('tracking_index_holdings_conflict')
    root=root or (strong['group_id'] if strong else None)
    if root not in roots:
        return pending('tracking_index_or_holdings_evidence_insufficient')
    if field=='fund_name' and not strong:
        return pending('fund_name_only_requires_source_corroboration')
    confidence=.9 if candidates and field=='tracking_index_name' else .8 if candidates else strong['exposure_lower_bound_pct']/100
    evidence['matched_subindustries']=[n['node_id'] for n in sub]
    evidence['agreement']='index_sector' if candidates else 'holdings_lower_bound'
    return {'state':'classified','group_id':root,'group_name':roots[root]['name'],
            'group_type':'industry','taxonomy_node_id':root,'confidence':confidence,
            'method_version':VERSION,'evidence':evidence}
