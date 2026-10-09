import json
import pytest
from guanlan_data.config import configure
from guanlan_data.doctor import roles
from guanlan_app.features.observer.data import DataModules

def test_old_config_does_not_require_resolve_or_open_retired_file(tmp_path):
    path=tmp_path/'config.json';path.write_text(json.dumps({'schema_version':'guanlan.config.v1',
        'paths':{'market_root':'data','state_root':'state','industry_db':'missing/retired.sqlite'}}),'utf-8')
    c=configure(path)
    assert 'industry_db' not in c.paths and 'industry' not in roles()
    assert not (tmp_path/'missing').exists()

def test_old_run_request_explicitly_rejected_before_current_read():
    d=DataModules()
    with pytest.raises(ValueError,match='退役'):d.query('industry30',{'view':'summary','run_id':'legacy'})
    d.close()
