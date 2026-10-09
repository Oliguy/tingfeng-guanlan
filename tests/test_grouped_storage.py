import json
import sqlite3
from pathlib import Path
import pytest
from guanlan_data.config import configure, data_path
from guanlan_data import sqlite as database
from guanlan_data.doctor import roles

def test_grouped_roles_and_write_isolation(tmp_path):
    root=tmp_path/'data';root.mkdir()
    layout={'schema_version':'stock.storage-layout.v1','activation':'active',
      'prefixes':{'market':'01_market/market','market_facts':'01_market/market_facts','market_etf':'02_industry/market_etf','industry/collections':'02_industry/collections','analysis':'03_indicators/analysis'},
      'aliases':{'classification/business.sqlite':'02_industry/business.sqlite','industry/support.sqlite':'01_market/market_facts/equity_status_daily.sqlite'},'stores':[]}
    (root/'storage_layout.v1.json').write_text(json.dumps(layout))
    cfg=tmp_path/'grouped.json'
    cfg.write_text(json.dumps({'schema_version':'guanlan.config.v1','paths':{'storage_root':str(root),'state_root':str(root/'04_user_state')}}))
    c=configure(cfg);assert c.path('market_root')==root/'01_market'
    assert roles()['status']==roles()['support']
    assert data_path('market/equity_daily_raw.sqlite')==root/'01_market/market/equity_daily_raw.sqlite'
    etf=roles()['etf'];etf.parent.mkdir(parents=True)
    with sqlite3.connect(etf) as connection:connection.execute('CREATE TABLE fact(id INTEGER)')
    with pytest.raises(PermissionError):database.connect(etf)
    connection=database.connect(etf.as_uri()+'?mode=ro',uri=True)
    try:
        with pytest.raises(sqlite3.DatabaseError):connection.execute('INSERT INTO fact VALUES(1)')
    finally:connection.close()
