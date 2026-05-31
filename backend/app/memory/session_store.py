from datetime import datetime, timezone
from typing import Any, Dict, List

_MAX_ENTRIES = 20
_session_history: List[Dict[str, Any]] = []


def add_session_entry(
    ticker: str,
    recommendation: str,
    risk_level: str,
) -> Dict[str, Any]:
    entry = {
        "ticker": ticker.upper(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recommendation": recommendation,
        "risk_level": risk_level,
    }
    _session_history.insert(0, entry)
    while len(_session_history) > _MAX_ENTRIES:
        _session_history.pop()
    return entry


def get_session_history() -> List[Dict[str, Any]]:
    return list(_session_history)
