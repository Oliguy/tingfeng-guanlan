"""Causal projection: finished bars + the one known opening price only."""
from datetime import date as Date
from decimal import Decimal
from guanlan_data.repositories.training.data import readonly; from guanlan_data.repositories.training.data import validate_bar
from guanlan_domain.training.rules import digest; from guanlan_domain.training.rules import decimal

from guanlan_data.training_history import next_date, history

def project(data, session, *, period='daily'):
    if period not in ('daily','weekly','monthly'):raise ValueError('图表周期无效')
    if hasattr(data,'prepare'):
        data=data.prepare(session)
    cache_key=(session['date'],session['phase'],period)
    cached=getattr(data,'projections',{}).get(period)
    if cached and cached[0]==cache_key:return cached[1]
    p=data.point(session['code'],session['date'],session['phase'])
    background,rows=history(data,session['code'],session['start'],session['date'],session['phase'])
    anchor=Decimal(str(background[-1]['close']))*Decimal(str(background[-1]['factor']))
    volume_base=sum(float(r['vol_lot']) for r in background)/len(background) or 1
    def normalized(value,factor):return float(Decimal(100)*Decimal(str(value))*Decimal(str(factor))/anchor)
    def bar(r,i):
        label=f'背景 {i+1:03d}' if i<120 else f'第 {i-119:03d} 日'
        return {'trade_date':label,'open':normalized(r['open'],r['factor']),
                'high':normalized(r['high'],r['factor']),'low':normalized(r['low'],r['factor']),
                'close':normalized(r['close'],r['factor']),'volume':float(r['vol_lot'])/volume_base}
    bars=[bar(r,i) for i,r in enumerate(rows)]
    if period!='daily':
        groups=[];previous=None
        for i,r in enumerate(rows):
            day=Date.fromisoformat(r['trade_date'])
            key=day.isocalendar()[:2] if period=='weekly' else (day.year,day.month)
            b=bars[i]
            if key!=previous:groups.append(dict(b,period_start=b['trade_date']));previous=key
            else:
                group=groups[-1];group.update(high=max(group['high'],b['high']),low=min(group['low'],b['low']),close=b['close'],volume=group['volume']+b['volume'],trade_date=b['trade_date'])
        bars=groups
    for i,b in enumerate(bars):
        for n in (5,20):b['ma'+str(n)]=sum(r['close'] for r in bars[i-n+1:i+1])/n if i+1>=n else None
    indicator_status={}
    if hasattr(data,'initialization'):
        initialization=data.initialization(session['date'],session['phase'])
        indicator_rows=data.overlays(initialization,period,normalized)
        lookup={r['trade_date']:r for r in indicator_rows}
        fields=('z_zhixing_short_trend','z_zhixing_bull_bear','thirty_week_ma','bbi')
        # The exposed candles still contain only the 120 background bars and
        # revealed training prefix; earlier prices only initialize the lines.
        end_dates=[]
        for r in rows:
            key=r['trade_date'] if period=='daily' else Date.fromisoformat(r['trade_date']).isocalendar()[:2] if period=='weekly' else r['trade_date'][:7]
            if not end_dates or end_dates[-1][0]!=key:end_dates.append([key,r['trade_date']])
            else:end_dates[-1][1]=r['trade_date']
        for b,(_,day) in zip(bars,end_dates):
            values=lookup.get(day,{})
            b.update({field:values.get(field) for field in fields})
        indicator_status={field:'可用' if bars and bars[-1].get(field) is not None else '历史不足或日历缺口' for field in fields}
    row=p['row'];quotes={'open':normalized(row['open'],row['factor'])}
    if session['phase']=='CLOSE':
        quotes.update({k:normalized(row[k],row['factor']) for k in ('high','low','close')})
        quotes['volume']=float(row['vol_lot'])/volume_base
    price=quotes['open' if session['phase']=='OPEN' else 'close']
    opening={'price':quotes['open'],'label':f"第 {session['day']:03d} 日开盘"} if session['phase']=='OPEN' else None
    result={'bars':bars,'quotes':quotes,'opening':opening,'price':price,
            'raw_price':row['open' if session['phase']=='OPEN' else 'close'],
            'multiplier':str(Decimal(100)*Decimal(str(row['factor']))/anchor),'rule':p['rule'],
            'gate':p['gate'],'rule_kind':p['rule']['kind'],'rule_origin':p['rule'].get('origin','unknown'),
            'period':period,'point_hash':p['fingerprint'],'history_hash':digest(rows),'background_hash':digest(background),
            'indicator_status':indicator_status}
    if hasattr(data,'fingerprints'):
        result.update(data.fingerprints(session));data.projections[period]=(cache_key,result)
    return result
