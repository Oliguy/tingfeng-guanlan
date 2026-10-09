"""Bounded, ephemeral one-symbol windows. Never persist market rows."""
from guanlan_data import sqlite as database
from collections import OrderedDict
from pathlib import Path
import json, sqlite3
from guanlan_data.repositories.training.data import readonly; from guanlan_data.repositories.training.data import validate_bar
from guanlan_domain.training.rules import limits; from guanlan_domain.training.rules import digest; from guanlan_domain.training.rules import point_gate; from guanlan_domain.training.rules import decimal

HISTORY_FIELDS=('trade_date','open','high','low','close','vol_lot','factor')

class Sources:
    """File identities plus live RO data_version; versions are not persisted."""
    def __init__(self,paths):
        self.paths=paths;self.connections={};self.previous=None;self.epoch=0
    def close(self):
        for _,c in self.connections.values():c.close()
        self.connections.clear()
    def poll(self):
        files={};versions={}
        for key,path in self.paths.items():
            if key=='store':continue
            path=Path(path).resolve()
            for suffix in ('','-wal'):
                target=Path(str(path)+suffix)
                try:
                    stat=target.stat();files[key+suffix]=[str(target),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]
                except FileNotFoundError:files[key+suffix]=None
            identity=files[key][:3] if files[key] else None
            old=self.connections.get(key)
            if old and old[0]!=identity:
                old[1].close();self.connections.pop(key);old=None
            if not old:
                c=database.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5,check_same_thread=False)
                c.execute('PRAGMA query_only=ON');old=(identity,c);self.connections[key]=old
            versions[key]=old[1].execute('PRAGMA data_version').fetchone()[0]
            # A first RO WAL reader can materialize WAL bookkeeping. Establish
            # the baseline after opening it, rather than report our own read.
            for suffix in ('','-wal'):
                target=Path(str(path)+suffix)
                try:
                    stat=target.stat();files[key+suffix]=[str(target),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]
                except FileNotFoundError:files[key+suffix]=None
        value=(files,versions)
        if self.previous is not None and value!=self.previous:self.epoch+=1
        self.previous=value
        return files

class Window:
    def __init__(self,data,session):
        self.paths=data.paths;self.code=session['code'];self.start=session['start'];self.projections={}
        with readonly(self.paths['raw']) as c:
            self.calendar_days=dict(c.execute("SELECT cal_date,is_open FROM trade_calendar WHERE exchange='SSE' ORDER BY cal_date"))
            self.calendar=[d for d,v in self.calendar_days.items() if v==1]
            remaining=[d for d in self.calendar if d>=self.start]
            length=session['settings']['length']
            end=session.get('window_end') or (remaining[min(length,len(remaining))-1] if remaining else session['date'])
            end=max(end,session['date'])
            self.rows=[dict(r) for r in c.execute('SELECT d.trade_date,d.open,d.high,d.low,d.close,d.pre_close,d.vol_lot,a.adj_factor AS factor FROM equity_daily_raw d LEFT JOIN equity_adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date WHERE d.ts_code=? AND d.trade_date<=? ORDER BY d.trade_date',(self.code,end))]
            row=c.execute('SELECT ts_code,name,market,list_date,delist_date,list_status FROM equity_master WHERE ts_code=?',(self.code,)).fetchone()
            if not row:raise ValueError('证券身份来源缺失')
            self.master=dict(row)
        self.by_date={r['trade_date']:r for r in self.rows}
        self.past=[r for r in self.rows if r['trade_date']<self.start]
        self.background_rows=[self.history_row(r) for r in self.past[-120:]]
        if len(self.background_rows)!=120:raise ValueError('120日背景行情或因子不完整')
        for row in self.background_rows:validate_bar(row)
        self.future=[r for r in self.rows if r['trade_date']>=self.start]
        self.end=end
        with readonly(self.paths['support']) as c:
            self.direct={r['trade_date']:dict(date=r['trade_date'],up=r['up_limit'],down=r['down_limit'],status=r['status']) for r in c.execute('SELECT trade_date,up_limit,down_limit,status FROM movers_limit_prices WHERE ts_code=? AND trade_date>=? AND trade_date<=?',(self.code,self.start,end))}
        iso=lambda d:d if '-' in d else d[:4]+'-'+d[4:6]+'-'+d[6:]
        with readonly(self.paths['status']) as c:
            self.status={r['trade_date']:{k:r[k] for k in ('is_st','is_delisted')} for r in c.execute('SELECT trade_date,is_st,is_delisted FROM equity_status_daily WHERE exchange=? AND stock_code=? AND trade_date>=? AND trade_date<=?',(self.code.split('.')[1],self.code[:6],iso(self.start),iso(end)))}
            self.names=[dict(r) for r in c.execute('SELECT name,start_date,end_date,change_reason FROM namechange_source WHERE ts_code=?',(self.code,))]
        self.rule_cache={};self.hash_cache={};self.background_hash=digest(self.background_rows)
        self.initialization_rows=[self.history_row(r) for r in self.rows]
        self.indicator_cache={};self.normalized_rows=[];self.indicator_hash_cache=None;self.daily_rows=[];self.overlay_accumulator=None
        from guanlan_domain.observer_calendar.core import Calendar
        self.indicator_calendar=Calendar(self.calendar_days)
        # Explicit JSON byte accounting is conservative for resident Python objects.
        self.size_bytes=len(json.dumps([self.rows,self.calendar_days,self.status,self.names,self.direct],ensure_ascii=False).encode())*16
    @staticmethod
    def history_row(row):return {k:row[k] for k in HISTORY_FIELDS}
    def background(self,*args):return self.background_rows
    def history(self,code,start,current,phase):
        rows=self.background_rows+[self.history_row(r) for r in self.future if r['trade_date']<current or phase=='CLOSE' and r['trade_date']==current]
        for row in rows:validate_bar(row)
        return self.background_rows,rows
    def initialization(self,current,phase):
        return [r for r in self.initialization_rows if r['trade_date']<current or phase=='CLOSE' and r['trade_date']==current]
    def overlays(self,initialization,period,normalize):
        """Keep only one known cutoff per period; initialize older closes once."""
        from guanlan_domain.observer_math.indicators import chart_rows; from guanlan_domain.observer_math.indicators import apply_overlays; from guanlan_domain.observer_math.indicators import weekly_line; from guanlan_domain.observer_math.indicators import OverlayAccumulator
        cutoff=initialization[-1]['trade_date']
        cached=self.indicator_cache.get(period)
        if cached and cached[0]==cutoff:return cached[1]
        if len(self.normalized_rows)>len(initialization):
            self.normalized_rows=[];self.daily_rows=[];self.overlay_accumulator=None
        for r in initialization[len(self.normalized_rows):]:
            item={'trade_date':r['trade_date'],'volume':0,'amount':0}
            for field in ('open','high','low','close'):
                item[field]=normalize(r[field],r['factor']) if decimal(r[field]) is not None and decimal(r['factor']) is not None else None
            self.normalized_rows.append(item)
        cal=self.indicator_calendar
        # Calendar caches are also bounded to the current cutoff, not every turn.
        cal._weeks_cache.clear()
        daily_cached=self.indicator_cache.get('daily')
        if daily_cached and daily_cached[0]==cutoff:daily=daily_cached[1]
        else:
            if self.overlay_accumulator is None:self.overlay_accumulator=OverlayAccumulator()
            for r in self.normalized_rows[len(self.daily_rows):]:
                self.daily_rows.append({**r,**self.overlay_accumulator.append(r['close'])})
            _,line=weekly_line(self.normalized_rows,cal)
            for r in self.daily_rows:r['thirty_week_ma']=line.get(r['trade_date'])
            daily=self.daily_rows
        self.indicator_cache['daily']=(cutoff,daily)
        if period=='daily':result=daily
        elif period=='weekly':result=chart_rows(self.normalized_rows,'weekly',cal)
        else:
            months=[]
            for r in daily:
                key=r['trade_date'][:7]
                if not months or months[-1]['key']!=key:months.append(dict(r,key=key))
                else:months[-1].update(trade_date=r['trade_date'],close=r['close'],thirty_week_ma=r['thirty_week_ma'])
            result=apply_overlays(months,{r['trade_date']:r['thirty_week_ma'] for r in months})
        self.indicator_cache[period]=(cutoff,result)
        return result
    def next_date(self,current):return next((d for d in self.calendar if d>current),None)
    def rule(self,code,day,pre_close):
        if day not in self.rule_cache:
            iso=day if '-' in day else day[:4]+'-'+day[4:6]+'-'+day[6:]
            names=[r for r in self.names if r['start_date']<=iso and (r['end_date'] is None or r['end_date']>=iso)]
            direct=self.direct.get(day);status=self.status.get(iso);name=names[0]['name'] if len(names)==1 else ''
            special=any('重新上市' in (r['change_reason'] or '') or '恢复上市' in (r['change_reason'] or '') for r in names)
            if direct is None and len(names)!=1:status=None
            result=limits(code=code,date=day,pre_close=pre_close,list_date=(self.master['list_date'] or '').replace('-',''),status=status,historical_name=name,direct=direct,special=special)
            evidence={'identity':{k:self.master[k] for k in ('ts_code','market','list_date')},'status':status,'name':name,'special':special,'direct':direct,'pre_close':pre_close,'rule':result}
            self.rule_cache[day]=(result,digest(evidence))
        return self.rule_cache[day]
    def point(self,code,day,phase):
        full=self.by_date.get(day)
        if not full or decimal(full['factor']) is None:raise ValueError('当前行情或复权因子缺失，已暂停')
        fields=('open','pre_close','factor') if phase=='OPEN' else ('open','high','low','close','pre_close','vol_lot','factor')
        row={k:full[k] for k in fields};rule,rule_hash=self.rule(code,day,row['pre_close'])
        gate=point_gate(row['open' if phase=='OPEN' else 'close'],rule)
        if not gate['valid']:raise ValueError(gate['buy']+'，已暂停')
        if phase=='CLOSE':validate_bar(row)
        return {'row':row,'rule':rule,'gate':gate,'fingerprint':digest({'row':row,'rule_hash':rule_hash})}
    def fingerprints(self,session):
        key=(session['date'],session['phase'])
        if key not in self.hash_cache:
            p=self.point(self.code,*key);_,history=self.history(self.code,self.start,*key)
            initialization=self.initialization(*key)
            cutoff=initialization[-1]['trade_date']
            if not self.indicator_hash_cache or self.indicator_hash_cache[0]!=cutoff:
                known_calendar={d:v for d,v in self.calendar_days.items() if initialization[0]['trade_date']<=d<=cutoff}
                self.indicator_hash_cache=(cutoff,digest({'rows':[{k:r[k] for k in ('trade_date','close','factor')} for r in initialization], 'calendar':known_calendar}))
            self.hash_cache[key]={'point_hash':p['fingerprint'],'history_hash':digest(history),'background_hash':self.background_hash,
                'indicator_hash':self.indicator_hash_cache[1]}
        return self.hash_cache[key]

class Windows:
    MAX_ENTRIES=4
    MAX_BYTES=64*1024*1024
    def __init__(self,data):self.data=data;self.sources=Sources(data.paths);self.entries=OrderedDict();self.epoch=-1
    def signature(self):
        signature=self.sources.poll()
        if self.epoch!=self.sources.epoch:self.entries.clear();self.epoch=self.sources.epoch
        return signature
    def prepare(self,s,*,checked=False):
        before=self.sources.previous[0] if checked and self.sources.previous else self.signature()
        key=(s['code'],s['start'],s.get('window_end'),s['settings']['length'])
        epoch=self.epoch
        window=self.entries.pop(key,None)
        if window is None:
            window=Window(self.data,s)
            if before!=self.signature() or epoch!=self.epoch:raise ValueError('行情来源正在变化，请稍后重试')
            if window.size_bytes>self.MAX_BYTES:raise ValueError('本局必要历史超过内存准备上限，请选择其他样本')
        self.entries[key]=window
        while len(self.entries)>self.MAX_ENTRIES or sum(v.size_bytes for v in self.entries.values())>self.MAX_BYTES:self.entries.popitem(last=False)
        return window
    def close(self):self.entries.clear();self.sources.close()
