"""Deterministic market and event intelligence. No prediction, no advice."""

from __future__ import annotations

CALCULATION_VERSION = "v1"
VOLUME_LOOKBACK = 20
VOLATILITY_LOOKBACK = 20
EVENT_WINDOW_SESSIONS = 5
DAILY_INTERVAL = "1d"

__all__ = [
    "CALCULATION_VERSION",
    "VOLUME_LOOKBACK",
    "VOLATILITY_LOOKBACK",
    "EVENT_WINDOW_SESSIONS",
    "DAILY_INTERVAL",
]
