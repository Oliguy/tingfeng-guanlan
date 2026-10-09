"""Create small, clearly synthetic databases in a NEW directory only.

No source database is opened, copied, exported, or sampled. The calendar is
weekdays only and all prices are mathematical examples, never real market data.
"""
from contextlib import closing
from datetime import date,timedelta
import json
import math
from pathlib import Path
import sqlite3
from .config import example,configure
from .doctor import contract,roles

def create(directory):
    root=Path(directory).expanduser().resolve()
    # Deliberately no exist_ok: never initialize/overwrite an existing data folder.
    root.mkdir(parents=True,exist_ok=False)
    file=root/'guanlan.json';config=example();config['demo']=True
    file.write_text(json.dumps(config,ensure_ascii=False,indent=2),'utf-8');configure(file)
    specs=contract()['roles']
    for role,path in roles().items():
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as c:
            for table,columns in specs[role]['tables'].items():
                fields=['"'+col['name']+'" '+(col['type'] or 'TEXT') for col in columns]
                pk=[x['name'] for x in sorted(columns,key=lambda x:x['pk']) if x['pk']]
                if pk:fields.append('PRIMARY KEY('+','.join('"'+x+'"' for x in pk)+')')
                c.execute('CREATE TABLE "'+table+'" ('+','.join(fields)+')')
            c.commit()
    def insert(c,role,table,values):
        cols=specs[role]['tables'][table]
        row={col['name']:('' if any(s in col['type'].upper() for s in ('TEXT','CHAR')) else 0) for col in cols if col['notnull']}
        row.update(values)
        c.execute('INSERT INTO "'+table+'" ('+','.join('"'+k+'"' for k in row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
    first=date(2023,1,2);end=date(2026,12,31);days=[];calendar=[];day=first
    while day<=end:
        opened=int(day.weekday()<5);calendar.append((day.isoformat(),opened))
        if opened and day<=date(2026,9,30):days.append(day.isoformat())
        day+=timedelta(days=1)
    codes=['600001.SH','000001.SZ','300001.SZ']
    with closing(sqlite3.connect(roles()['equity'])) as c,c:
        for day,opened in calendar:insert(c,'equity','trade_calendar',{'exchange':'SSE','cal_date':day,'is_open':opened})
        for index,code in enumerate(codes):
            insert(c,'equity','equity_master',{'ts_code':code,'name':'合成演示'+str(index+1),'market':'创业板' if code.startswith('3') else '主板','list_date':'2000-01-01','list_status':'L','delist_date':None})
            prev=10+index*3
            for n,day in enumerate(days):
                close=round(10+index*3+n*.008+math.sin(n*.13+index)*.35,2)
                op=round((prev+close)/2,2)
                insert(c,'equity','equity_daily_raw',{'ts_code':code,'trade_date':day,'open':op,'high':max(op,close)+.1,'low':min(op,close)-.1,'close':close,'pre_close':prev,'vol_lot':1000+n%80,'amount_thousand_cny':close*100,'pct_chg':(close/prev-1)*100})
                insert(c,'equity','equity_adj_factor',{'ts_code':code,'trade_date':day,'adj_factor':1})
                prev=close
        c.execute('CREATE INDEX demo_equity_date ON equity_daily_raw(trade_date)')
    with closing(sqlite3.connect(roles()['status'])) as c,c:
        for code in codes:
            for day in days:insert(c,'status','equity_status_daily',{'exchange':code.split('.')[1],'stock_code':code[:6],'trade_date':day,'is_st':0,'is_delisted':0})
            insert(c,'status','namechange_source',{'ts_code':code,'name':'合成演示','start_date':'2000-01-01','end_date':None,'change_reason':'synthetic'})
        for k,v in {'coverage_start':days[0],'coverage_end':days[-1],'st_source_mode':'synthetic-demo'}.items():
            insert(c,'status','metadata',{'key':k,'value':v})
    with closing(sqlite3.connect(roles()['etf'])) as c,c:
        insert(c,'etf','schema_meta',{'key':'schema_version','value':'4'})
        insert(c,'etf','observer_meta',{'singleton':1,'input_revision':0,'data_revision':0})
    with closing(sqlite3.connect(roles()['results'])) as c,c:
        insert(c,'results','series_checkpoints',{'key':'schema','value_json':json.dumps('observer_series_raw_week_v1')})
    rules=json.loads((Path(__file__).parent/'rules/industries_30.v1.json').read_text('utf-8'))['sectors']
    with closing(sqlite3.connect(roles()['business'])) as c,c:
        insert(c,'business','sp_taxonomy_revisions',{'id':1,'created_at':'2026-01-01T00:00:00Z','reason':'synthetic demo'})
        for i,r in enumerate(rules):
            insert(c,'business','sp_rules',{'scheme':'trading30','version':'trading_industries_30_v1','industry':r['id'],'name':r['name'],'inclusion':r['includes'],'exclusion':r['excludes']})
            insert(c,'business','sp_taxonomy_nodes',{'revision_id':1,'node_id':r['id'],'parent_id':None,'name':r['name'],'position':i,'enabled':1,'aliases_json':'[]','rule_json':'{}'})
    (root/'SYNTHETIC-DATA.txt').write_text('全部行情、状态和日历均为程序生成的演示数据；非真实市场，不含用户原库。\nETF、行业和题材结果未伪造，初始列表为空。\n','utf-8')
    return str(file)
