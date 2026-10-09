"""Industry paths from the explicit database configuration."""
from guanlan_data.layout import resolve_data_path
from pathlib import Path
from guanlan_data.config import current

def operator_project():return current().file.parent
def result_path():raise ValueError('旧行业结果库已退役；使用统一集合发布')
def input_paths():return current().path('business_db'),resolve_data_path(current().path('storage_root'),'market/equity_daily_raw.sqlite')
