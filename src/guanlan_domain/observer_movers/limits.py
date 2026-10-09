import math
from decimal import Decimal, ROUND_HALF_UP

STATES={'confirmed_up':'收盘涨停','not_limit':'未涨停','unrestricted':'当日无涨跌停限制','unknown':'涨停待核验','conflict':'证据冲突'}

def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def same_price(a,b):
    if not finite(a) or not finite(b) or a<=0 or b<=0:return False
    tick=Decimal('.01')
    return Decimal(str(a)).quantize(tick,rounding=ROUND_HALF_UP)==Decimal(str(b)).quantize(tick,rounding=ROUND_HALF_UP)

def valid_limit(value):
    # Canonical A-share prices use a cent tick. Reject provider sentinel prices
    # (for example 999999.999) instead of treating them as a real price cap.
    return finite(value) and value>0 and Decimal(str(value))==Decimal(str(value)).quantize(Decimal('.01'))

