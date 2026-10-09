"""Complete, cutoff-bound chart history from original sources; never writes caches."""
import math
from guanlan_data.repositories.industry_index.inputs import readonly,digest
from guanlan_data.repositories.observer_calendar import load
from guanlan_domain.observer_math import chart_rows
from guanlan_domain.observer_math.indicators import weekly_line,apply_overlays,align_weeks
from guanlan_domain.observer_math.signals import price_strength
from guanlan_domain.observer_math.chart_metrics import enrich

def stock_history(market,code,end,period='daily',mode='adjusted'):
    with readonly(market) as c:
        rows=[dict(r) for r in c.execute('SELECT r.*,f.adj_factor FROM equity_daily_raw r LEFT JOIN equity_adj_factor f USING(ts_code,trade_date) WHERE r.ts_code=? AND r.trade_date<=? ORDER BY r.trade_date',(code,end))]
        calendar=load(market,connection=c)
    if not rows:raise ValueError('该股票没有本地历史行情')
    anchor=rows[-1].get('adj_factor') if mode=='adjusted' else 1
    if not isinstance(anchor,(int,float)) or not math.isfinite(anchor) or anchor<=0:raise ValueError('缺少截至日复权锚点，可选择原始价格查看')
    history=[];events=[]
    for r in rows:
        factor=r.get('adj_factor') if mode=='adjusted' else 1
        valid=isinstance(factor,(int,float)) and math.isfinite(factor) and factor>0 and all(isinstance(r[k],(int,float)) and math.isfinite(r[k]) and r[k]>0 for k in ('open','high','low','close'))
        if not valid:
            events.append({'trade_date':r['trade_date'],'kind':'missing','label':'历史因子或行情缺失，未补造价格'})
            history.append({'trade_date':r['trade_date'],**dict.fromkeys(('open','high','low','close','volume','amount')),'status':'missing','calendar_gap':True})
            continue
        change=r.get('pct_chg')/100 if isinstance(r.get('pct_chg'),(int,float)) and math.isfinite(r['pct_chg']) else r['close']/r['pre_close']-1 if r.get('pre_close') else None
        history.append({'trade_date':r['trade_date'],**{k:r[k]*factor/anchor for k in ('open','high','low','close')},'volume':r['vol_lot']*100 if r.get('vol_lot') is not None else None,'amount':r['amount_thousand_cny']*1000 if r.get('amount_thousand_cny') is not None else None,'change_pct':change})
    observed={r['trade_date']:r for r in history}
    for day in calendar.sessions(rows[0]['trade_date'],end):
        if day not in observed:
            observed[day]={'trade_date':day,**dict.fromkeys(('open','high','low','close','volume','amount')),'status':'missing','calendar_gap':True}
            events.append({'trade_date':day,'kind':'missing','label':'无日线，未推断停牌'})
    history=[observed[d] for d in sorted(observed)]
    # Missing-price placeholders belong to the daily display, not the weekly
    # aggregator: counting them as observed sessions would manufacture low=0.
    valid_history=[r for r in history if r.get('status')!='missing']
    weekly,line=weekly_line(valid_history,calendar)
    weekly=align_weeks(weekly,calendar,start=history[0]['trade_date'],end=end)
    computed=apply_overlays(weekly if period=='weekly' else history,line)
    points=enrich(computed,period,calendar)
    return {'bars':points,'rows':rows,'events':events,'calendar':calendar,'signal':price_strength(valid_history,calendar),'revision':digest([rows,calendar.revision,mode]),'start':rows[0]['trade_date']}
