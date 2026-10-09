"""Nominal fractional units and causal normalized prices in one price domain."""
from decimal import Decimal, ROUND_HALF_UP

def D(value):return Decimal(str(value))
def initial(capital=100000,*,origin_capital=None,prior_fees=0):
    capital=D(capital);origin=D(origin_capital) if origin_capital is not None else capital
    if not capital.is_finite() or capital<0 or not origin.is_finite() or origin<=0:raise ValueError('训练资金无效')
    return {'cash':str(capital),'units':'0','buy_day':None,'cost':'0','fees':'0',
            'initial_capital':str(capital),'origin_capital':str(origin),'prior_fees':str(D(prior_fees))}
def numbers(account,price,day):
    units,cash=D(account['units']),D(account['cash']);free=units if account['buy_day'] is not None and account['buy_day']<day else Decimal(0)
    nav=cash+units*D(price)
    base=D(account.get('initial_capital',100000));origin=D(account.get('origin_capital',100000))
    return {'cash':float(cash),'units':float(units),'unlocked':float(free),'frozen':float(units-free),
            'nav':float(nav),'return':float((nav/base-1)*100) if base else 0,'exposure':float(units*D(price)/nav*100) if nav else 0,
            'initial_capital':float(base),'origin_capital':float(origin),'profit':float(nav-base),
            'cumulative_return':float((nav/origin-1)*100),'cumulative_profit':float(nav-origin),
            'cost':float(D(account['cost'])),'fees':float(D(account['fees'])),
            'cumulative_fees':float(D(account.get('prior_fees',0))+D(account['fees']))}
def reasons(account,point,day):
    stats=numbers(account,point['price'],day)
    return {'BUY':point['gate']['buy'] or ('已有持仓 · 不再加仓' if stats['units']>0 else '可用资金不足' if stats['cash']<=0 else ''),
            'SELL':point['gate']['sell'] or ('无持仓可卖' if stats['units']<=0 else 'T+1 · 今日不可卖' if stats['unlocked']<=0 else ''),
            'HOLD':''}
def execution_price(point, action, slip):
    price=D(point['price'])
    if not slip or action=='HOLD':return price
    # Slippage is a cost model at the known auction point. Tick rounding and
    # the verified daily limits must be applied in the original price domain.
    rule=point['rule'];raw=D(point['raw_price']);multiplier=D(point['multiplier'])
    if rule['kind'] not in ('limited','unlimited'):raise ValueError('滑点成交规则未核实')
    fill=(raw*(1+slip if action=='BUY' else 1-slip)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    if rule['kind']=='limited':fill=max(D(rule['down'])/100,min(D(rule['up'])/100,fill))
    return fill*multiplier
def execute(account, action, point, day, *, fee_bps=0, slippage_bps=0):
    if action not in ('BUY','SELL','HOLD'):raise ValueError('操作无效')
    blocked=reasons(account,point,day)[action]
    if blocked:raise ValueError(blocked)
    new=dict(account);price=D(point['price']);rate=D(fee_bps)/10000;slip=D(slippage_bps)/10000
    units,fee=Decimal(0),Decimal(0);fill=execution_price(point,action,slip)
    if action=='BUY':
        cash=D(account['cash']);units=cash/(fill*(1+rate));fee=units*fill*rate
        new.update(cash='0',units=str(units),buy_day=day,cost=str(cash/units),fees=str(D(account['fees'])+fee))
    elif action=='SELL':
        units=D(account['units']);fee=units*fill*rate
        new.update(cash=str(D(account['cash'])+units*fill-fee),units='0',buy_day=None,cost='0',fees=str(D(account['fees'])+fee))
    receipt={'action':action,'price':float(fill),'units':float(units),'fee':float(fee),'nav':numbers(new,price,day)['nav']}
    return new,receipt
