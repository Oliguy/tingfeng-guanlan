"""Pinned pure ETF display rules; no external imports or data access."""

from datetime import date, timedelta

DISPLAY_GAP_THRESHOLD = 0.20

def continuous_adjust_bars(rows):
    """Back-adjust OHLC for display while retaining raw exchange values separately."""
    bars = [dict(row) for row in rows]
    if not bars:
        return [], []
    factors = [1.0] * len(bars)
    events = []
    running_factor = 1.0
    for index in range(len(bars) - 1, 0, -1):
        factors[index] = running_factor
        current_open = bars[index].get("open")
        previous_close = bars[index - 1].get("close")
        if current_open and previous_close:
            boundary_factor = float(current_open) / float(previous_close)
            if abs(boundary_factor - 1.0) > DISPLAY_GAP_THRESHOLD:
                events.append(
                    {
                        "trade_date": bars[index]["trade_date"],
                        "factor": boundary_factor,
                        "previous_close": previous_close,
                        "current_open": current_open,
                        "reason": "unit_price_discontinuity",
                    }
                )
                running_factor *= boundary_factor
    factors[0] = running_factor
    for bar, factor in zip(bars, factors):
        for field in ("open", "high", "low", "close", "prev_close"):
            if bar.get(field) is not None:
                bar[field] = float(bar[field]) * factor
        bar["display_adjustment_factor"] = factor
        bar["is_adjustment_boundary"] = any(event["trade_date"] == bar["trade_date"] for event in events)
    events.sort(key=lambda event: event["trade_date"])
    return bars, events

def week_start(value: str) -> str:
    day = date.fromisoformat(value)
    return (day - timedelta(days=day.weekday())).isoformat()

def weekly_rows(rows: list[dict]) -> list[dict]:
    weeks: list[dict] = []
    for row in rows:
        key = week_start(row["trade_date"])
        if not weeks or weeks[-1]["week_start"] != key:
            weeks.append({**row, "week_start": key, "period_start": row["trade_date"]})
        else:
            item = weeks[-1]
            item.update(trade_date=row["trade_date"], close=row["close"],
                        high=max(item["high"] or 0, row["high"] or 0),
                        low=min(item["low"] or 0, row["low"] or 0),
                        volume=(item.get("volume") or 0)+(row.get("volume") or 0),
                        amount=(item.get("amount") or 0)+(row.get("amount") or 0))
    return weeks
