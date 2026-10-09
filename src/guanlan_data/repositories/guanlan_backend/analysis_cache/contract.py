"""The compact analysis dataset's public field and price contract."""
from __future__ import annotations
from datetime import date, datetime
from pathlib import Path
SCHEMA_VERSION = 'stock-analysis-basis-v1'
PRICE_COLUMNS = ('open', 'high', 'low', 'close', 'ma5', 'ma10', 'ma20', 'ma60', 'z_zhixing_short_trend', 'z_zhixing_bull_bear')
VALUE_COLUMNS = PRICE_COLUMNS[:4] + ('vol_lot', 'amount_thousand_cny', 'pct_chg') + PRICE_COLUMNS[4:]
COLUMNS = ('ts_code', 'trade_date') + VALUE_COLUMNS
PERIODS = (5, 10, 20, 60, 14, 28, 57, 114)
UNITS = {**{name: 'CNY * cumulative adjustment factor' for name in PRICE_COLUMNS}, 'vol_lot': 'lot (100 shares)', 'amount_thousand_cny': '1000 CNY', 'pct_chg': 'percent, supplier daily change', 'trade_date': 'date', 'ts_code': 'six-digit code.exchange'}
FIELD_CONTRACT = {name: {'dtype': 'float64', 'unit': UNITS[name], 'scale_degree': int(name in PRICE_COLUMNS), 'nullable': name == 'pct_chg'} for name in VALUE_COLUMNS}
DEFAULT_VERSION_LIMIT = 2 * 1024 ** 3
DEFAULT_ROOT_LIMIT = 4 * 1024 ** 3
DEFAULT_ARRAY_LIMIT = 3 * 1024 ** 3

def iso_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    value = str(value)
    return datetime.strptime(value, '%Y%m%d').date().isoformat() if len(value) == 8 and value.isdigit() else date.fromisoformat(value).isoformat()

def default_paths() -> tuple[Path, Path]:
    from guanlan_data.config import data_path
    from guanlan_data.config import current
    return (data_path('market', 'equity_daily_raw.sqlite'), current().path('analysis_root'))
