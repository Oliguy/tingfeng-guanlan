"""Bounded readonly stock-date facts, sharing the leader's price rules."""
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
import sqlite3
from guanlan_data.repositories.industry_index.inputs import readonly
from guanlan_data.repositories.observer_calendar import load
from guanlan_data.repositories.observer_movers.leader_reader import _price_support
from guanlan_domain.observer_movers.limit_rules import decide
from guanlan_domain.observer_movers.sequence import sequence

def read(reader,day,codes):
    if not codes:return {},{}
    with readonly(reader.market) as c:
        calendar=load(reader.market,connection=c)
        sessions=list(calendar.sessions('1900-01-01',day))[-25:]
        if not sessions:return {},{'status':'unavailable','reason':'calendar_missing'}
        placeholders=','.join('?' for _ in codes)
        rows=c.execute('SELECT r.*,m.name,m.list_date FROM equity_daily_raw r LEFT JOIN equity_master m USING(ts_code) WHERE r.ts_code IN ('+placeholders+') AND r.trade_date BETWEEN ? AND ? ORDER BY r.trade_date',(*codes,sessions[0],day))
        series=defaultdict(dict)
        for row in rows:series[row['ts_code']][row['trade_date']]=dict(row)
    errors=[];states={}
    try:support=_price_support(reader.support,sessions[0],day)
    except (OSError,sqlite3.Error):support={};errors.append('限制价支持库读取失败')
    if Path(reader.support).exists() and not errors:
        with readonly(reader.support) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='equity_status_daily'").fetchone():
                for r in c.execute('SELECT exchange,stock_code,trade_date,is_st FROM equity_status_daily WHERE trade_date BETWEEN ? AND ?',(sessions[0],day)):
                    market={'SSE':'SH','SZSE':'SZ','BSE':'BJ','SH':'SH','SZ':'SZ','BJ':'BJ'}.get(r['exchange'])
                    if market:states[(r['stock_code'].zfill(6)+'.'+market,r['trade_date'])]=bool(r['is_st'])
    result={};window=sessions[-20:];index={d:i for i,d in enumerate(sessions)}
    for code in codes:
        history=series[code];ordered=sorted(history);facts=[]
        for d in window:
            row=history.get(d)
            fact=decide(row,list_date=row.get('list_date'),prior_quote_days=bisect_left(ordered,d),session_index=index,support=support.get((code,d)),name=row.get('name'),is_st=states.get((code,d))) if row else {'status':'unknown','reason':'missing_session_quote','sources':[]}
            if errors:fact={'status':'unknown','reason':'dated_support_unavailable','sources':[]}
            facts.append({'date':d,**fact})
        current=facts[-1];current={**current,'label':{'confirmed_up':'收盘涨停','not_limit':'未涨停','unrestricted':'无涨跌停限制','unknown':'证据不足','conflict':'证据冲突'}[current['status']]}
        result[code]={'limit':current,'limit_sequence':sequence(facts)}
    return result,{'status':'unavailable' if errors else 'local_calculated','errors':errors,'window':window,'calendar_revision':calendar.revision}
