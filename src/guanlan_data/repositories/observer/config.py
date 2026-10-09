"""Compatibility facade for feature readers; all configuration belongs to data."""
from pathlib import Path
from guanlan_data.config import current
from guanlan_data.repositories.industry_index.config import operator_project; from guanlan_data.repositories.industry_index.config import result_path
HERE=Path(__file__).parent

def settings():
    c=current()
    return {'data_root':str(c.path('market_root')),'runtime_root':str(c.state),
            'project_root':str(c.file.parent),'workspace_root':str(c.file.parent)}

def jobs_root():return current().path('jobs_root')

def installed_library(*args):
    raise ValueError('便携版通过显式操作服务连接更新提供者，不加载本机安装目录')

def etf_command():
    raise ValueError('请配置 provider 连接现有更新服务')
