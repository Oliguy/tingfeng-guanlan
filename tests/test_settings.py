import json
from pathlib import Path
from unittest.mock import patch
import pytest
from guanlan_domain.settings import SCHEMA,DEFAULTS,document,preferences
from guanlan_data.settings_store import SettingsStore,saved_data
from guanlan_app.settings import SettingsService,portable
from guanlan_data.config import configure,load,settings_file
from .test_runtime import running,raw,Echo

def payload(data=None,**p):
    return {'schema_version':SCHEMA,'preferences':{**DEFAULTS,**p},'data':data or {'mode':'local'}}

def test_file_restart_and_old_chart_fields_preserved(tmp_path):
    store=SettingsStore(tmp_path/'settings.json',{'mode':'local'})
    saved=store.save(payload(zoomGesture='alt',showBbi=False,fontSize=16),'absent')
    again=SettingsStore(store.path,{'mode':'local'}).read()
    assert again['revision']==saved['revision'] and again['preferences']['showBbi'] is False
    assert again['preferences']['zoomGesture']=='alt' and again['preferences']['fontSize']==16
    assert store.path.exists() and saved_data(store.path)['mode']=='local'

def test_stale_window_cannot_overwrite(tmp_path):
    one=SettingsStore(tmp_path/'settings.json',{'mode':'local'});two=SettingsStore(one.path,{'mode':'local'})
    old=two.read();one.save(payload(fontSize=14),old['revision'])
    before=one.path.read_bytes()
    with pytest.raises(ValueError,match='其他窗口'):two.save(payload(fontSize=12),old['revision'])
    assert one.path.read_bytes()==before

def test_failed_replace_preserves_old_file_and_removes_temp(tmp_path):
    store=SettingsStore(tmp_path/'settings.json',{'mode':'local'});s=store.save(payload(),'absent');before=store.path.read_bytes()
    with patch('guanlan_data.settings_store.os.replace',side_effect=PermissionError('synthetic denied')):
        with pytest.raises(PermissionError):store.save(payload(fontSize=14),s['revision'])
    assert store.path.read_bytes()==before and not list(tmp_path.glob('*.tmp'))

def test_corrupt_configuration_requires_explicit_import_recovery(tmp_path):
    p=tmp_path/'settings.json';p.write_text('{broken','utf-8');s=SettingsStore(p,{'mode':'local'});old=s.read()
    assert not old['valid'] and saved_data(p) is None
    with pytest.raises(ValueError):s.save(payload(),old['revision'])
    assert p.read_text('utf-8')=='{broken'
    s.save(payload(fontSize=14),old['revision'],recover=True)
    assert s.read()['valid'] and list(tmp_path.glob('*.invalid-*'))[0].read_text('utf-8')=='{broken'

@pytest.mark.parametrize('change',[{'fontSize':True},{'showBbi':'yes'},{'defaultRange':9999},{'zhixingWhiteWidth':float('nan')},{'unknown':1}])
def test_bad_preferences_rejected(change):
    with pytest.raises(ValueError):preferences(change)

def test_import_version_and_secrets_rejected():
    with pytest.raises(ValueError):document({'schema_version':'v999'})
    with pytest.raises(ValueError):document({**payload(),'token':'private'})
    with pytest.raises(ValueError):document(payload({'mode':'remote','url':'https://user:password@example.invalid'}))

def test_check_is_read_only_and_preferences_do_not_submit_data(tmp_path):
    calls=[]
    service=SettingsService(tmp_path/'settings.json',{'mode':'local'},lambda data:calls.append(data) or {'status':'PASS'})
    first=service.read()
    assert service.invoke({'action':'check','data':first['data']},local_owner=True)['status']=='PASS'
    assert not service.store.path.exists()
    saved=service.invoke({'action':'preferences','revision':'absent','preferences':{**DEFAULTS,'fontSize':14}},local_owner=True)
    assert len(calls)==1 and not saved['restart_required']
    with pytest.raises(ValueError,match='宿主'):service.invoke({'action':'data','revision':saved['revision'],'data':{'mode':'local'}},local_owner=False)

def test_http_settings_use_auth_and_local_store_not_remote_forward(tmp_path):
    service=SettingsService(tmp_path/'settings.json',{'mode':'local'},lambda _: {'status':'PASS'})
    echo=Echo()
    with running(service=echo) as s:
        s.settings_service=service
        _,_,h=raw(s,'GET','/api/v1/health')
        body={'action':'preferences','revision':'absent','preferences':{**DEFAULTS,'density':'compact'}}
        assert raw(s,'POST','/api/v1/settings',json.dumps(body),{'Content-Type':'application/json'})[0]==403
        result=raw(s,'POST','/api/v1/settings',json.dumps(body),{'Content-Type':'application/json','X-Stock-Operator-Write-Token':h['write_token']})
        assert result[2]['ok'] and not echo.requests
        assert raw(s,'GET','/api/v1/settings')[2]['data']['preferences']['density']=='compact'

def test_data_overlay_only_takes_effect_after_reload_and_leaves_config_untouched(tmp_path):
    from guanlan_data.demo import create
    a=create(tmp_path/'one');b=create(tmp_path/'two');c=configure(a)
    before=c.file.read_bytes();s=portable(c);original=c.path('storage_root')
    proposal={**s.read()['data'],'storage_root':str(load(b).path('storage_root'))}
    result=s.invoke({'action':'data','revision':'absent','data':proposal},local_owner=True)
    assert result['restart_required'] and c.path('storage_root')==original
    assert load(a).path('storage_root')==load(b).path('storage_root') and c.file.read_bytes()==before
    assert settings_file(c.file,c.raw).exists()
    original_file=settings_file(c.file,c.raw).read_bytes()
    with pytest.raises(ValueError,match='不存在'):
        s.invoke({'action':'data','revision':result['revision'],'data':{**proposal,'storage_root':str(tmp_path/'missing')}},local_owner=True)
    assert settings_file(c.file,c.raw).read_bytes()==original_file
