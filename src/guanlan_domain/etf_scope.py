"""Pure ETF asset/market screening; no external taxonomy dependency."""
import re
from typing import Any, Mapping
EXCLUSION_TERMS = ('货币', '债券', '国债', '信用债', '可转债', '黄金', '白银', '有色期货', '原油', '豆粕', '商品', 'REIT', '纳斯达克', '标普', '日经', '德国', '法国', '香港', '港股', '恒生', '海外', 'QDII', '短融', '郑商所', '上期有色', '上海金', '上海银')
BROAD_INDEX_NAMES = ('沪深300', '500', '1000', '2000', '800', 'A50', 'A100', 'A500', 'A股', '上证50', '上证180', '上证580', '上证综合', '上证科创板综合', '上证科创板综合价格', '上证中盘', '上证超级大盘', '科创50', '科创创业50', '上证科创板100', '上证科创板200', '创业板50', '创业板200', '创业板300', '创业板综合', '创业板大盘', '深证50', '深证100', '深证成份价格', '深证300价格', '深证主板50', '中小企业100', '中小创业企业400', '创业板价格', '深证100价格', '上证科创板50成份', '创业板中盘精选88', '500等权重', '上证180公司治理')
STRATEGY_TERMS = ('自由现金流', '现金流', '红利', '价值', '成长', '低波', '质量', '高股息', '基本面', 'ESG', '责任指数', '动量', '核心竞争力', '可持续发展')
NON_INDUSTRY_THEME_TERMS = ('央企', '国企', '民企', '一带一路', '长三角', '大湾区', '杭州湾', '成渝', '湖北', '浙江', 'G60', '民族品牌', '央视财经', '小康产业', '国有企业改革')
MULTI_INDUSTRY_THEME_TERMS = ('科技龙头', '科技100', '科技50', '科技先锋', '科技优势', '创新100', '创新驱动', '战略新兴', '产业升级', '创业板科技', '科技传媒通信', 'TMT')
CROSS_BORDER_TERMS = ('沪港深', '沪深港', '深港', '沪港', '港股通', '富时中国国企')
SECTOR_MANDATE_TERMS = ('科技', '能源', '煤炭', '保险', '钢铁', '机器人')
INDUSTRY_KEYWORDS = (('金融与金融科技', ('金融科技', '证券公司', '金融股', '券商', '证券', '银行')), ('通信与网络', ('通信', '电信', '卫星', '物联网', '5G', '6G')), ('电子与半导体', ('半导体', '集成电路', '芯片', '电子50')), ('人工智能与数字经济', ('软件服务', '软件开发', '信息安全', '网络安全', '云计算', '数字经济', '人工智能', '软件')), ('传媒文娱与教育', ('传媒', '文娱', '动漫', '游戏', '教育')), ('新能源与电力', ('光伏', '风电', '新能源', '电力', '电网', '储能', '电池')), ('环保与公用事业', ('长江保护', '环保', '碳中和', '低碳', '公用事业')), ('汽车与智能交通', ('汽车零部件', '智能汽车', '智能驾驶', '车联网', '汽车')), ('高端装备与国防军工', ('高端装备', '高端制造', '智能制造', '工业机械', '装备产业', '机械', '机床', '船舶', '军工', '国防', '航空航天')), ('能源资源', ('稀有金属', '稀土', '有色金属', '有色', '钢铁', '石化', '资源')), ('基础化工与新材料', ('新材料', '化工', '材料')), ('地产基建与建筑', ('建筑材料', '房地产', '地产', '基建', '建材', '建筑')), ('交通运输与物流', ('交通运输', '现代物流', '物流', '运输', '交运')), ('农业与食品', ('农牧', '农业', '养殖', '食品', '酒')), ('医药生物', ('生物医药', '创新药', '中药', '医药', '医疗')), ('消费品与零售', ('家用电器', '家居家电', '家电', '可选消费', '消费', '旅游', '家居', '零售')))
SEMANTIC_EQUIVALENTS = (('集成电路', '半导体'), ('芯片', '半导体'), ('券商', '证券'), ('医疗健康', '医药'), ('医疗', '医药'), ('电动车', '新能源车'), ('新能源汽车', '新能源车'), ('智能汽车', '汽车'), ('云服务', '云计算'))

def _normalize(text: object) -> str:
    value = re.sub('[\\s·（）()、/\\-_]+', '', str(text or '')).upper()
    for term in ('交易型开放式', '指数证券投资基金', 'ETF', '指数', '收益率', '主题', '概念', '中证', '国证', '全指'):
        value = value.replace(term.upper(), '')
    for source, target in SEMANTIC_EQUIVALENTS:
        value = value.replace(source.upper(), target.upper())
    return value

class ScopeRules:
    @staticmethod
    def scope_dimensions(profile: Mapping[str, Any]) -> dict[str, Any]:
        text = ' '.join((str(profile.get(k) or '') for k in ('fund_name', 'fund_type', 'tracking_index_name', 'benchmark', 'catalog_fund_name', 'catalog_fund_type')))
        upper = text.upper()
        sectors = sorted({keyword for _, keywords in INDUSTRY_KEYWORDS for keyword in keywords if keyword.upper() in upper} | {term for term in SECTOR_MANDATE_TERMS if term in text})
        strategies = [term for term in STRATEGY_TERMS if term.upper() in upper]
        commodity = any((t in upper for t in ('黄金', '白银', '原油', '豆粕', '商品', '上海金', '上海银')))
        equity_mandate = any((t in upper for t in ('矿业', '产业', '股票', '油气开采', '石油天然气')))
        asset = 'money_market' if '货币' in text else 'fixed_income' if any((t in text for t in ('债券', '国债', '短融', '信用债'))) else 'real_estate_trust' if 'REIT' in upper else 'commodity' if commodity and (not equity_mandate) else 'equity_candidate' if equity_mandate or sectors else 'unknown'
        foreign = any((t in upper for t in ('QDII', '纳斯达克', '标普', '日经', '德国', '法国', '香港', '港股', '恒生', '海外')))
        mixed = any((t in upper for t in ('沪港深', '沪深港', '深港', '沪港', '港股通')))
        return {'asset_type': asset, 'investment_market': 'mixed_mainland_hong_kong' if mixed else 'overseas_or_hong_kong' if foreign else 'mainland_candidate', 'sector_terms': sectors, 'strategy_terms': strategies, 'evidence_basis': 'profile_and_name_screening; component evidence determines exposure'}

    @staticmethod
    def _exclusion_reason(profile: Mapping[str, Any], text: str) -> str | None:
        text = ' '.join((text, str(profile.get('catalog_fund_name') or ''), str(profile.get('catalog_fund_type') or '')))
        upper = text.upper()
        for term in EXCLUSION_TERMS:
            if term in {'黄金', '白银', '原油', '商品'} and any((item in upper for item in ('矿业', '产业', '股票', '油气开采', '石油天然气'))):
                continue
            if term.upper() in upper:
                return f'excluded_asset_scope:{term}'
        index_scope = str(profile.get('tracking_index_name') or profile.get('benchmark') or profile.get('fund_name') or '')
        normalized_scope = _normalize(index_scope)
        sector_mandate = any((keyword.upper() in index_scope.upper() for _, keywords in INDUSTRY_KEYWORDS for keyword in keywords)) or any((term in index_scope for term in SECTOR_MANDATE_TERMS))
        for term in CROSS_BORDER_TERMS:
            if term.upper() in index_scope.upper():
                return f'excluded_cross_border:{term}'
        if normalized_scope in {_normalize(term) for term in BROAD_INDEX_NAMES}:
            return f'excluded_broad_index:{index_scope}'
        for term in STRATEGY_TERMS:
            if term.upper() in index_scope.upper():
                if sector_mandate:
                    continue
                return f'excluded_factor_or_strategy:{term}'
        for term in NON_INDUSTRY_THEME_TERMS:
            if term.upper() in index_scope.upper():
                if sector_mandate:
                    continue
                return f'excluded_non_industry_theme:{term}'
        if text and '股票' not in text and any((term in text for term in ('债', '货币', '商品'))):
            return 'excluded_non_equity'
        return None
