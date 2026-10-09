import hashlib,json,sqlite3
from contextlib import closing
from pathlib import Path
import pytest
from guanlan_data.config import configure
from guanlan_data.demo import create
from guanlan_data.repositories.observer_chart_history import stock_history
from guanlan_data.repositories.stock_profile.working_index_snapshot import read
from guanlan_data.repositories.guanlan_backend.market_etf.observer_read_repository import mapped_quote_leader
from guanlan_domain.observer_movers.limit_rules import decide
from guanlan_domain.observer_movers.sequence import sequence

def test_complete_history_cutoff_and_missing_factor_are_read_only(tmp_path):
    c=configure(create(tmp_path/'demo'));market=c.path('market_root')/'market/equity_daily_raw.sqlite'
    with closing(sqlite3.connect(market)) as db,db:
        db.execute("DELETE FROM equity_adj_factor WHERE ts_code='600001.SH' AND trade_date='2023-01-03'")
    before=hashlib.sha256(market.read_bytes()).hexdigest()
    d=stock_history(market,'600001.SH','2026-08-31','daily','adjusted')
    assert d['bars'][0]['trade_date']=='2023-01-02'
    assert d['bars'][-1]['trade_date']=='2026-08-31' and len(d['bars'])>900
    gap=next(r for r in d['bars'] if r['trade_date']=='2023-01-03')
    assert gap['close'] is None and gap['status']=='missing'
    raw=stock_history(market,'600001.SH','2026-08-31','daily','raw')
    assert next(r for r in raw['bars'] if r['trade_date']=='2023-01-03')['close'] is not None
    w=stock_history(market,'600001.SH','2026-08-31','weekly','adjusted')
    assert len(w['bars'])>150 and w['bars'][-1]['trade_date']<='2026-08-31'
    assert hashlib.sha256(market.read_bytes()).hexdigest()==before

def test_moved_database_uses_explicit_frozen_reference_and_checks_hash(tmp_path):
    cfg=tmp_path/'guanlan.json';cfg.write_text(json.dumps({'schema_version':'guanlan.config.v1','paths':{'business_db':'moved/business.sqlite','classification_root':'frozen','market_root':'data','state_root':'state'}}),'utf-8')
    c=configure(cfg);root=c.path('classification_root');root.mkdir()
    (root/'snapshot.json').write_text('{}','utf-8')
    (root/'root_current.json').write_text(json.dumps({'file':'snapshot.json','sha256':'0'*64}),'utf-8')
    with pytest.raises(ValueError,match='快照校验失败'):read(c.path('business_db'),[])
    assert read(tmp_path/'unrelated.sqlite',[]) is None

def test_st_price_limit_uses_date_fact_and_ipo_exemption():
    row={'ts_code':'600001.SH','trade_date':'2026-10-09','open':10.1,'high':10.5,'low':10,'close':10.5,'pre_close':10,'vol_lot':100,'amount_thousand_cny':1000}
    assert decide(row,list_date='20000101',prior_quote_days=20,is_st=True)['status']=='not_limit'
    assert decide(row,list_date='20000101',prior_quote_days=20,is_st=False)['status']=='not_limit'
    old={**row,'trade_date':'2026-07-03'}
    assert decide(old,list_date='20000101',prior_quote_days=20,is_st=True)['status']=='confirmed_up'
    assert decide(old,list_date='20000101',prior_quote_days=20,is_st=False)['status']=='not_limit'
    changed={**row,'high':11,'close':11}
    assert decide(changed,list_date='20000101',prior_quote_days=20,is_st=True)['status']=='confirmed_up'
    assert decide(row,list_date='20261009',session_index={'2026-10-09':0},prior_quote_days=0,is_st=False)['status']=='unrestricted'

def test_sequence_empty_no_board_and_no_false_certainty():
    days=[{'date':f'2026-09-{d:02}','status':'not_limit'} for d in range(21,26)]
    assert sequence(days)['label']==''
    days[-1]['status']='confirmed_up';assert sequence(days)['label']=='1天1板'
    days[-3]['status']='confirmed_up';assert sequence(days)['label']=='3天2板'
    days[-2]['status']='unknown';result=sequence(days)
    assert result['label']=='' and result['known_boards']==2 and len(result['unknown_dates'])==1

def test_etf_quote_fallback_requires_active_mapping_and_quotes():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE etf_group_membership(group_id,etf_code,status);CREATE TABLE etf_master(etf_code,fund_name,tracking_index_name);CREATE TABLE etf_daily(etf_code,trade_date,amount);')
    db.executemany('INSERT INTO etf_master VALUES(?,?,?)',[('159001','甲','A'),('159002','乙','B'),('159003','未映射','C')])
    db.executemany('INSERT INTO etf_group_membership VALUES(?,?,?)',[('I01','159001','active'),('I01','159002','retired')])
    db.executemany('INSERT INTO etf_daily VALUES(?,?,?)',[('159001','2026-10-09',10),('159002','2026-10-09',100),('159003','2026-10-09',1000)])
    row=mapped_quote_leader(db,'I01');assert row['etf_code']=='159001' and row['selection_kind']=='mapped_quote'
    assert mapped_quote_leader(db,'I02') is None
    db.close()
