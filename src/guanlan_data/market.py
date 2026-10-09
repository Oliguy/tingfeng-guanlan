"""Bounded stock-return repository used by ETF read projections."""
import re
from pathlib import Path
from contextlib import closing
import sqlite3
from . import sqlite as database

def load_latest_stock_returns(stock_codes, *, on_or_before=None, kline_db=None):
    from .config import data_path
    path=Path(kline_db) if kline_db else data_path('market','equity_daily_raw.sqlite')
    codes=sorted({str(c).zfill(6) for c in stock_codes if str(c).strip()})
    if len(codes)>10000 or any(not re.fullmatch(r'\d{6}',c) for c in codes):raise ValueError('股票代码范围无效')
    if on_or_before:
        from datetime import date
        if date.fromisoformat(on_or_before).isoformat()!=on_or_before:raise ValueError('日期无效')
    if not codes or not path.is_file():return {}
    result={}
    with closing(database.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN')
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='equity_daily_raw'").fetchone():return {}
        for start in range(0,len(codes),300):
            ts=[f'{code}.{exchange}' for code in codes[start:start+300] for exchange in ('SH','SZ','BJ')]
            rows=c.execute('''WITH ranked AS (SELECT ts_code,trade_date,close,pre_close,pct_chg,
                ROW_NUMBER() OVER(PARTITION BY substr(ts_code,1,6) ORDER BY trade_date DESC) AS rn
                FROM equity_daily_raw WHERE ts_code IN ('''+','.join('?' for _ in ts)+')'+
                (' AND trade_date<=?' if on_or_before else '')+
                ') SELECT substr(ts_code,1,6) AS code,trade_date,close,pre_close,pct_chg FROM ranked WHERE rn=1',
                [*ts,*([on_or_before] if on_or_before else [])])
            for r in rows:
                value=float(r['pct_chg'])/100 if r['pct_chg'] is not None else float(r['close'])/float(r['pre_close'])-1 if r['pre_close'] not in (None,0) and r['close'] is not None else None
                result[r['code']]={'daily_return':value,'trade_date':str(r['trade_date'])}
    return result
