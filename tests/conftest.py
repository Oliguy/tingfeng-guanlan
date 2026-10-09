"""Legacy fixtures create synthetic databases only under the OS temporary folder."""
import json
from pathlib import Path
import tempfile
import pytest
from guanlan_data.config import configure

@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path):
    file=tmp_path/'test-config.json'
    # Existing unit fixtures each allocate their own TemporaryDirectory.
    value={'schema_version':'guanlan.config.v1','paths':{
        'market_root':str(Path(__file__).parent/'unconfigured-source'),
        'state_root':tempfile.gettempdir()}}
    file.write_text(json.dumps(value),'utf-8');configure(file)
    yield
