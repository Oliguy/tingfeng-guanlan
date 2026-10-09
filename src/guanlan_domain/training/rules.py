"""Date-specific cent-exact limits. No daily high/low/close input here."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

VERSION = 'tingfeng_auction_daily_20261006_v1'
SOURCES = [
 'https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml',
 'https://www.szse.cn/aboutus/trends/news/t20200821_580924.html',
 'https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf',
]

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def decimal(value):
    try:
        x = Decimal(str(value))
        return x if x.is_finite() and x > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def cents(value):
    x = decimal(value)
    return int(x * 100) if x is not None and x * 100 == (x * 100).to_integral_value() else None

def unknown(reason):
    return {'kind': 'unknown', 'reason': reason, 'version': VERSION}

def limits(*, code, date, pre_close, list_date, status, historical_name='', direct=None,
           special=False, listing_day=None, explicit_unlimited=False):
    """direct must be dated to this point. Missing rows never mean unlimited."""
    ref = cents(pre_close)
    if direct is not None:
        if direct.get('date') != date or direct.get('status') != 'available':
            return unknown('当日限制价证据缺失')
        up, down = cents(direct.get('up')), cents(direct.get('down'))
        if up is None or down is None or not down < up or (ref and not down <= ref <= up):
            return unknown('限制价证据冲突')
        return {'kind': 'limited', 'up': up, 'down': down, 'origin': 'direct', 'version': VERSION}
    date, list_date = date.replace('-', ''), (list_date or '').replace('-', '')
    if not list_date or list_date > date:
        return unknown('历史上市身份缺失')
    if special or '退' in historical_name or (historical_name.startswith('S') and not historical_name.startswith('ST')):
        return unknown('特殊上市或退市期间规则未核实')
    if explicit_unlimited:
        return {'kind': 'unlimited', 'origin': 'explicit', 'version': VERSION}
    if listing_day is not None and listing_day <= 5:
        return unknown('上市初期规则未核实')
    if ref is None:
        return unknown('原始前收基准缺失或不在分位')
    if code.endswith('.BJ'):
        return unknown('北交所历史规则待核实')
    if not code.endswith(('.SH', '.SZ')) or status is None:
        return unknown('历史状态证据缺失')
    if status.get('is_delisted'):
        return unknown('历史退市状态')
    st = status.get('is_st')
    if st not in (0, 1):
        return unknown('历史风险警示状态未知')
    if historical_name and ('ST' in historical_name.upper()) != bool(st):
        return unknown('历史名称与风险警示状态冲突')
    if code.startswith(('688', '689')) and code.endswith('.SH'):
        if date < '20190722': return unknown('科创板制度日期异常')
        rate = Decimal('.20')
    elif code.startswith(('300', '301', '302')) and code.endswith('.SZ'):
        rate = Decimal('.20') if date >= '20200824' else Decimal('.05' if st else '.10')
    elif code[:3] in ('600', '601', '603', '605', '000', '001', '002', '003'):
        rate = Decimal('.05' if st and date < '20260706' else '.10')
    else:
        return unknown('板块身份未核实')
    base = Decimal(ref) / 100
    up = int((base * (1 + rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) * 100)
    down = int((base * (1 - rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) * 100)
    up, down = max(up, ref + 1), max(1, min(down, ref - 1))
    return {'kind': 'limited', 'up': up, 'down': down, 'origin': 'derived',
            'rate': str(rate), 'version': VERSION}

def point_gate(raw_price, rule):
    p = cents(raw_price)
    if p is None: return {'buy': '当前报价缺失或损坏', 'sell': '当前报价缺失或损坏', 'valid': False}
    if rule['kind'] == 'unknown':
        reason = '规则未核实 · 仅可观望'
        return {'buy': reason, 'sell': reason, 'valid': True}
    if rule['kind'] == 'limited':
        if not rule['down'] <= p <= rule['up']:
            return {'buy': '报价与限制价冲突', 'sell': '报价与限制价冲突', 'valid': False}
        return {'buy': '涨停 · 禁止买入' if p == rule['up'] else '',
                'sell': '跌停 · 禁止卖出' if p == rule['down'] else '', 'valid': True}
    return {'buy': '', 'sell': '', 'valid': True}
