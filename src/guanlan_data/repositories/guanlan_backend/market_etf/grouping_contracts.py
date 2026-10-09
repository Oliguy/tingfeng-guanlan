"""Stable grouping value encodings; no persistence or source access."""
import json
from datetime import datetime, timedelta, timezone
from typing import Any
VERSION = 'industry_etf_grouping_v3'

def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))

def knowledge_date(timestamp: str) -> str:
    """Public date-only queries and memberships use the A-share business timezone."""
    instant = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone(timedelta(hours=8))).date().isoformat()
