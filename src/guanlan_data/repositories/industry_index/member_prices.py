"""Frozen member quotes from the same repaired inputs as the industry index."""
import json
import math
import zlib
from guanlan_data.repositories.industry_index.inputs import canonical; from guanlan_data.repositories.industry_index.inputs import readonly
from guanlan_domain.industry_index.indicators import calculator

FIELDS = ('trade_date','open','high','low','close','adj_factor','vol_lot','amount_thousand_cny','price_hash','factor_hash')

def freeze(market):
    result = {}
    for code, rows in market['quotes'].items():
        history = [{k: row.get(k) for k in FIELDS} for day,row in sorted(rows.items())]
        last = history[-1] if history else {}
        previous = history[-2] if len(history)>1 else {}
        denominator = (previous.get('close') or 0)*(previous.get('adj_factor') or 0)
        change = last['close']*last['adj_factor']/denominator-1 if denominator and last.get('adj_factor') else None
        result[code] = {'rows':history,'suspensions':market.get('suspensions',{}).get(code,{}),
                        'summary':{'date':last.get('trade_date'),'close':last.get('close'),'change':change,
                                   'amount':last.get('amount_thousand_cny',0)*1000,
                                   'volume':last.get('vol_lot',0)*100},'metadata':market['metadata'].get(code)}
    return result

def encoded(payload): return zlib.compress(canonical(payload).encode(),6)

def summaries(path, run_id):
    with readonly(path) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='industry_member_prices'").fetchone():return {}
        return {row[0]:json.loads(row[1]) for row in c.execute('SELECT code,summary_json FROM industry_member_prices WHERE run_id=?',(run_id,))}

def bars(path, run_id, code, start, period='daily', mode='adjusted', sessions=None):
    if period not in ('daily','weekly') or mode not in ('adjusted','raw'):raise ValueError('个股周期或价格口径无效')
    with readonly(path) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='industry_member_prices'").fetchone():raise ValueError('当前行业版本还没有成员行情，请在更新状态中选择仅重算行业')
        row=c.execute('SELECT payload FROM industry_member_prices WHERE run_id=? AND code=?',(run_id,code)).fetchone()
    if not row:raise ValueError('此版本没有该成员的行情快照')
    payload=json.loads(zlib.decompress(row[0]));raw=payload['rows']
    if not raw:return [],payload,[]
    anchor=raw[-1].get('adj_factor') if mode=='adjusted' else 1
    if not anchor or not math.isfinite(anchor) or anchor<=0:raise ValueError('缺少复权锚点，请使用原始价格或更新数据')
    history=[]
    for r in raw:
        factor=r.get('adj_factor') if mode=='adjusted' else 1
        if not factor or not math.isfinite(factor) or factor<=0:raise ValueError('历史复权因子缺失，不能显示完整复权图')
        prices={k:r[k]*factor/anchor for k in ('open','high','low','close')}
        if any(not math.isfinite(v) or v<=0 for v in prices.values()):raise ValueError('个股行情价格无效')
        history.append({'trade_date':r['trade_date'],**prices,'volume':r['vol_lot']*100,'amount':r['amount_thousand_cny']*1000})
    calc=calculator();values=calc.chart_rows(history,period)
    selected=[r for r in values if r['trade_date']>=start]
    # Preserve time-axis gaps for confirmed halts without inventing traded OHLC.
    events=[{'trade_date':d,'kind':'suspended','label':'全天停牌，无成交'} for d in sorted(payload['suspensions']) if d>=start]
    if period=='daily':
        selected+= [{'trade_date':e['trade_date'],'open':None,'high':None,'low':None,'close':None,'volume':0,'amount':0,'status':'suspended'} for e in events if e['trade_date'] not in {r['trade_date'] for r in selected}]
        meta=payload.get('metadata') or {};listing=str(meta.get('list_date') or '9999-12-31')
        if len(listing)==8:listing=f'{listing[:4]}-{listing[4:6]}-{listing[6:]}'
        existing={r['trade_date'] for r in selected}
        for day in sessions or []:
            if day>=max(start,listing) and day not in existing:
                selected.append(dict(trade_date=day,open=None,high=None,low=None,close=None,volume=None,amount=None,status='missing'))
                events.append({'trade_date':day,'kind':'missing','label':'行情缺失，未作停牌估值'})
        selected.sort(key=lambda r:r['trade_date'])
    return selected,payload,events
