"""Industry paths from the explicit database configuration."""
from pathlib import Path
from guanlan_data.config import current

def operator_project():return current().file.parent
def result_path():return current().path('industry_db')
def input_paths():return current().path('business_db'),current().path('market_root')/'market/equity_daily_raw.sqlite'
