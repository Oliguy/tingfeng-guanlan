"""Dated limit evidence. Reading never creates a database or calls a provider."""
import math
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from guanlan_data.repositories.industry_index.inputs import readonly

from guanlan_domain.observer_movers.limits import STATES, finite, same_price, valid_limit

def read_day(support,kph,day,rows):
    limits={};positive={};receipt=None;errors=[]
    if Path(support).exists():
        try:
            with readonly(support) as c:
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='movers_limit_prices'").fetchone():
                    limits={r['ts_code']:dict(r) for r in c.execute('SELECT trade_date,ts_code,up_limit,down_limit,status,request_id,fetched_at FROM movers_limit_prices WHERE trade_date=?',(day,))}
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='movers_limit_days'").fetchone():
                        r=c.execute('SELECT * FROM movers_limit_days WHERE trade_date=?',(day,)).fetchone()
                        receipt=dict(r) if r else None
        except (sqlite3.Error,OSError):limits={};errors.append('限制价支持库读取失败，保留异动名单')
    if kph and Path(kph).exists():
        try:
            with readonly(kph) as c:
                positive={r['instrument_id']:dict(r) for r in c.execute('SELECT instrument_id,source,collected_at FROM daily_limit_up WHERE trade_date=? AND closed_at_limit=1',(day.replace('-',''),))}
        except (sqlite3.Error,OSError):errors.append('本地收盘涨停榜读取失败，未当作未涨停证据')
    result={}
    for row in rows:
        code=row['ts_code'];price=limits.get(code);k=positive.get(code)
        state='unknown';sources=[]
        if price:
            sources.append({'endpoint':'stk_limit','trade_date':day,'fetched_at':price['fetched_at'],'request_id':price['request_id']})
            if price['status']=='conflict':state='conflict'
            elif price['status']=='unrestricted':state='unrestricted'
            elif price['status']=='available' and valid_limit(price['up_limit']) and valid_limit(price['down_limit']) and finite(row['close']) and row['close']>0:
                # Never use yesterday's observed close or a percentage heuristic.
                state='confirmed_up' if same_price(row['close'],price['up_limit']) else 'not_limit'
            if k and state in ('not_limit','unrestricted'):state='conflict'
        if k:
            sources.append({'endpoint':'kph','trade_date':day,'fetched_at':k['collected_at'],'source':k['source']})
            if state=='unknown':state='confirmed_up'
        result[code]={'status':state,'label':STATES[state],'up_limit':price['up_limit'] if price else None,'sources':sources}
    if errors:receipt={**(receipt or {}),'status':'unavailable','message':'；'.join(errors)}
    return result,receipt
