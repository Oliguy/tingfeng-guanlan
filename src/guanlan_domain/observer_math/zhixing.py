"""Canonical chart formula, extracted without numerical changes from StockOperator."""
from typing import Iterable
ZHIXING_Z_PARAMS = {"M1": 14, "M2": 28, "M3": 57, "M4": 114}

def _ema_series(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result: list[float | None] = []
    ema = values[0]
    for value in values:
        ema = value * alpha + ema * (1.0 - alpha)
        result.append(ema)
    return result

def _zhixing_trend_series(closes: list[float]) -> tuple[list[float | None], list[float | None]]:
    short_trend = _ema_series([value if value is not None else 0.0 for value in _ema_series(closes, 10)], 10)
    ma_m1 = [_rolling_avg(closes, idx, ZHIXING_Z_PARAMS["M1"]) for idx in range(len(closes))]
    ma_m2 = [_rolling_avg(closes, idx, ZHIXING_Z_PARAMS["M2"]) for idx in range(len(closes))]
    ma_m3 = [_rolling_avg(closes, idx, ZHIXING_Z_PARAMS["M3"]) for idx in range(len(closes))]
    ma_m4 = [_rolling_avg(closes, idx, ZHIXING_Z_PARAMS["M4"]) for idx in range(len(closes))]
    bull_bear: list[float | None] = []
    for values in zip(ma_m1, ma_m2, ma_m3, ma_m4):
        if any(value is None for value in values):
            bull_bear.append(None)
        else:
            bull_bear.append(sum(value for value in values if value is not None) / 4.0)
    return short_trend, bull_bear

def calculate_zhixing_trend_series(
    closes: Iterable[float],
) -> tuple[list[float | None], list[float | None]]:
    """Calculate the canonical main-chart Zhixing white and yellow lines."""

    return _zhixing_trend_series([float(value) for value in closes])

def _rolling_avg(values: list[float] | list[int], idx: int, period: int) -> float | None:
    if idx < 0:
        return None
    start = max(0, idx - period + 1)
    window = values[start : idx + 1]
    return sum(window) / len(window) if window else None

