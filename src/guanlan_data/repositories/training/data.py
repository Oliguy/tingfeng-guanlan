"""Bounded original-path queries; never create/copy/write a market database."""
from guanlan_data.layout import resolve_data_path
from guanlan_data import sqlite as database
from contextlib import contextmanager
from pathlib import Path
import json
import random
import sqlite3
import threading
from guanlan_domain.training.rules import limits; from guanlan_domain.training.rules import digest; from guanlan_domain.training.rules import point_gate; from guanlan_domain.training.rules import decimal
from decimal import Decimal, InvalidOperation

def validate_bar(row):
    prices={k:decimal(row.get(k)) for k in ('open','high','low','close','factor')}
    if any(v is None for v in prices.values()):raise ValueError('日K价格或复权因子缺失、损坏，已暂停')
    try:volume=Decimal(str(row.get('vol_lot')))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('成交量缺失或损坏，已暂停') from None
    if not volume.is_finite() or volume<0:raise ValueError('成交量缺失或损坏，已暂停')
    if not prices['low']<=min(prices['open'],prices['close'])<=max(prices['open'],prices['close'])<=prices['high']:
        raise ValueError('日K字段冲突，已暂停')

@contextmanager
def readonly(path):
    connection = database.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    try:
        yield connection
    finally:
        connection.close()

def configured_paths():
    from guanlan_data.config import current
    c=current();root=c.path('storage_root')
    return {'raw':resolve_data_path(root,'market/equity_daily_raw.sqlite'),
            'status':resolve_data_path(root,'market_facts/equity_status_daily.sqlite'),
            'support':c.path('support_db'),'store':c.path('training_db')}

class Data:
    def __init__(self, paths=None):
        self.paths = paths or configured_paths()
        self._pool = None
        self._lock = threading.Lock()
        self._windows = None

    def windows(self):
        if self._windows is None:
            from guanlan_data.repositories.training.cache import Windows
            self._windows=Windows(self)
        return self._windows

    def prepare(self,session):return self.windows().prepare(session)
    def source_signature(self):return self.windows().signature()
    def close(self):
        if self._windows is not None:self._windows.close();self._windows=None

    def coverage(self):
        with readonly(self.paths['raw']) as c:
            first = c.execute('SELECT trade_date FROM equity_daily_raw ORDER BY trade_date LIMIT 1').fetchone()[0]
            last = c.execute('SELECT trade_date FROM equity_daily_raw ORDER BY trade_date DESC LIMIT 1').fetchone()[0]
        with readonly(self.paths['status']) as c:
            metadata = dict(c.execute('SELECT key,value FROM metadata'))
        return {'first': first, 'last': last, 'status_end': metadata['coverage_end'],
                'status_mode': metadata.get('st_source_mode'), 'source_paths': {k:str(v) for k,v in self.paths.items() if k!='store'}}

    def pool(self):
        if self._pool is not None: return self._pool
        with self._lock:
            if self._pool is None:
                with readonly(self.paths['raw']) as c:
                    masters = {r['ts_code']:dict(r) for r in c.execute('SELECT ts_code,name,market,list_date,delist_date,list_status FROM equity_master')}
                    eligible = {code for code in masters if c.execute('SELECT trade_date FROM equity_daily_raw WHERE ts_code=? ORDER BY trade_date LIMIT 1 OFFSET 120',(code,)).fetchone()}
                # Current metadata is used only for stable code/board/listing identity,
                # never as historical ST status. Delisted securities remain here.
                self._pool = [m for code,m in sorted(masters.items()) if code in eligible and m['list_date']]
        return self._pool

    def dates(self, code, until=None):
        with readonly(self.paths['raw']) as c:
            sql='SELECT trade_date FROM equity_daily_raw WHERE ts_code=?'
            args=[code]
            if until: sql+=' AND trade_date<=?';args.append(until)
            return [r[0] for r in c.execute(sql+' ORDER BY trade_date', args)]

    def identity(self, code):
        with readonly(self.paths['raw']) as c:
            row=c.execute('SELECT ts_code,name,market,list_date,delist_date,list_status FROM equity_master WHERE ts_code=?',(code,)).fetchone()
        if not row: raise ValueError('证券身份来源缺失')
        return dict(row)

    def rule(self, code, date, pre_close):
        with readonly(self.paths['support']) as c:
            row=c.execute('SELECT up_limit,down_limit,status FROM movers_limit_prices WHERE trade_date=? AND ts_code=?',(date,code)).fetchone()
        if row:
            direct={'date':date,'up':row[0],'down':row[1],'status':row[2]}
        else: direct=None
        identity=self.identity(code)
        with readonly(self.paths['status']) as c:
            exchange=code.split('.')[1]
            iso=date if '-' in date else date[:4]+'-'+date[4:6]+'-'+date[6:]
            row=c.execute('SELECT is_st,is_delisted FROM equity_status_daily WHERE exchange=? AND stock_code=? AND trade_date=?',(exchange,code[:6],iso)).fetchone()
            status=dict(row) if row else None
            names=list(c.execute('SELECT name,change_reason FROM namechange_source WHERE ts_code=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)',(code,iso,iso)))
        name=names[0][0] if len(names)==1 else ''
        special=any('重新上市' in (r[1] or '') or '恢复上市' in (r[1] or '') for r in names)
        # A missing/overlapping effective name is an incomplete exception check.
        if direct is None and (len(names)!=1): status=None
        result=limits(code=code,date=date,pre_close=pre_close,list_date=(identity['list_date'] or '').replace('-',''),status=status,historical_name=name,direct=direct,special=special)
        evidence={'identity':{k:identity[k] for k in ('ts_code','market','list_date')},'status':status,
                  'name':name,'special':special,'direct':direct,'pre_close':pre_close,'rule':result}
        return result, digest(evidence)

    def point(self, code, date, phase):
        fields='open,pre_close' if phase=='OPEN' else 'open,high,low,close,pre_close,vol_lot'
        with readonly(self.paths['raw']) as c:
            row=c.execute(f'SELECT {fields} FROM equity_daily_raw WHERE ts_code=? AND trade_date=?',(code,date)).fetchone()
            factor=c.execute('SELECT adj_factor FROM equity_adj_factor WHERE ts_code=? AND trade_date=?',(code,date)).fetchone()
        if not row or not factor or decimal(factor[0]) is None: raise ValueError('当前行情或复权因子缺失，已暂停')
        row=dict(row);row['factor']=factor[0]
        rule, rule_hash=self.rule(code,date,row['pre_close'])
        raw=row['open' if phase=='OPEN' else 'close']
        gate=point_gate(raw,rule)
        if not gate['valid']: raise ValueError(gate['buy']+'，已暂停')
        if phase=='CLOSE':validate_bar(row)
        return {'row':row,'rule':rule,'gate':gate,'fingerprint':digest({'row':row,'rule_hash':rule_hash})}

    def background(self, code, start):
        with readonly(self.paths['raw']) as c:
            rows=list(c.execute('SELECT d.trade_date,d.open,d.high,d.low,d.close,d.vol_lot,a.adj_factor AS factor FROM equity_daily_raw d LEFT JOIN equity_adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date WHERE d.ts_code=? AND d.trade_date<? ORDER BY d.trade_date DESC LIMIT 120',(code,start)))
        if len(rows)!=120 or any(decimal(r['factor']) is None for r in rows): raise ValueError('120日背景行情或因子不完整')
        background=[dict(r) for r in reversed(rows)]
        for row in background:validate_bar(row)
        return background

    def training_window(self,code,start,length,calendar=None):
        """Technical completeness only; no return or future-limit selection."""
        if calendar is None:
            with readonly(self.paths['raw']) as c:
                calendar=[r[0] for r in c.execute("SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>=? ORDER BY cal_date LIMIT ?",(start,length))]
        if len(calendar)!=length or calendar[0]!=start:raise ValueError(f'缺少完整{length}个训练交易日')
        with readonly(self.paths['raw']) as c:
            rows=[dict(r) for r in c.execute('SELECT d.trade_date,d.open,d.high,d.low,d.close,d.vol_lot,a.adj_factor AS factor FROM equity_daily_raw d LEFT JOIN equity_adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date WHERE d.ts_code=? AND d.trade_date>=? AND d.trade_date<=? ORDER BY d.trade_date',(code,start,calendar[-1]))]
        if [r['trade_date'] for r in rows]!=calendar:raise ValueError(f'缺少完整{length}根训练日K')
        for row in rows:validate_bar(row)
        return calendar[-1]

    def choose(self, seed, options):
        rng=random.Random(seed)
        pool=[m for m in self.pool() if m['ts_code']!=options.get('_exclude_code') and (options.get('market','all')=='all' or m['market']==options['market'])]
        rng.shuffle(pool)
        with readonly(self.paths['status']) as c:
            meta=dict(c.execute('SELECT key,value FROM metadata'))
        length=options.get('length',150)
        with readonly(self.paths['raw']) as c:
            calendar=[r[0] for r in c.execute("SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 ORDER BY cal_date")]
        if len(calendar)<length:raise ValueError(f'本地交易日历不足完整{length}个训练交易日')
        calendar_index={day:i for i,day in enumerate(calendar)};latest_start=calendar[-length]
        for m in pool:
            dates=self.dates(m['ts_code'])
            candidates=[d for d in dates[120:] if d<=latest_start and d in calendar_index]
            if not candidates:continue
            with readonly(self.paths['support']) as c:
                direct_dates={r[0] for r in c.execute("SELECT trade_date FROM movers_limit_prices WHERE ts_code=? AND status='available'",(m['ts_code'],))}
            # Limit/status eligibility uses start-day evidence only. The later
            # window check validates bar/factor completeness, not future gates.
            candidates=[d for d in candidates if d in direct_dates or (not m['ts_code'].endswith('.BJ') and meta['coverage_start']<=d<=meta['coverage_end'])]
            if options.get('from'): candidates=[d for d in candidates if d>=options['from']]
            if options.get('to'): candidates=[d for d in candidates if d<=options['to']]
            rng.shuffle(candidates)
            for date in candidates:
                try:
                    point=self.point(m['ts_code'],date,'OPEN')
                    if point['rule']['kind']=='unknown': continue
                    if options.get('exclude_st'):
                        # A five-percent historical rule is positive ST evidence;
                        # query historical status explicitly for other dates.
                        with readonly(self.paths['status']) as c:
                            iso=date if '-' in date else date[:4]+'-'+date[4:6]+'-'+date[6:]
                            st=c.execute('SELECT is_st FROM equity_status_daily WHERE stock_code=? AND trade_date=?',(m['ts_code'][:6],iso)).fetchone()
                        if not st or st[0]:continue
                    self.background(m['ts_code'],date)
                    index=calendar_index[date]
                    end=self.training_window(m['ts_code'],date,length,calendar[index:index+length])
                    return {'code':m['ts_code'],'start':date,'end':end,'sampling_version':'fixed_length_v2'}
                except ValueError: continue
        raise ValueError(f'所选范围没有可核实起点且具有120日背景、完整{length}根训练日K与因子的样本，请扩大范围')
