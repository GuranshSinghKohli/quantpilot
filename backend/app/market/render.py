"""Render a market observation from one stored impact window.

The model may discuss the window. It does not choose the percent. 0.015857
is +1.59% because this function multiplies the stored fraction by 100.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

WINDOW_ORDER = ("+1D", "+3D", "+5D", "-1D", "+20D")

WINDOW_LABELS = {
    "-1D": "-1 session",
    "+1D": "+1 session",
    "+3D": "+3 session",
    "+5D": "+5 session",
    "+20D": "+20 session",
}


def format_return_percent(value: float) -> str:
    percent = round(float(value) * 100, 2)
    if percent > 0:
        return f"+{percent:.2f}%"
    return f"{percent:.2f}%"


def render_market_statement(fact: Mapping[str, Any], window: str) -> Optional[str]:
    block = _block(fact, window)
    if block is None:
        return None
    asset = block.get("return")
    benchmark_return = block.get("benchmark_return")
    if asset is None or benchmark_return is None:
        return None
    name = str(fact.get("canonical_name") or fact.get("symbol") or "").strip()
    symbol = str(fact.get("symbol") or "").strip().upper()
    benchmark = str(
        fact.get("benchmark_symbol") or fact.get("configured_benchmark_symbol") or ""
    ).strip().upper()
    if not name or not symbol or not benchmark:
        return None
    label = WINDOW_LABELS.get(window, window)
    return (
        f"{name} ({symbol}) returned {format_return_percent(float(asset))} "
        f"over the {label} window, versus {format_return_percent(float(benchmark_return))} for {benchmark}."
    )


def market_observation(fact: Mapping[str, Any], window: str) -> Optional[Dict[str, Any]]:
    """One impact row, one window. Returns are copied, not recomputed."""
    statement = render_market_statement(fact, window)
    if statement is None:
        return None
    block = _block(fact, window) or {}
    event_id = str(fact.get("event_id") or (fact.get("source") or {}).get("event_id") or "")
    return {
        "statement": statement,
        "entity_id": str(fact.get("entity_id") or ""),
        "symbol": str(fact.get("symbol") or "").strip().upper(),
        "event_id": event_id,
        "window": window,
        "benchmark_symbol": str(
            fact.get("benchmark_symbol") or fact.get("configured_benchmark_symbol") or ""
        ).strip().upper(),
        "calculation_version": str(
            fact.get("calculation_version") or (fact.get("source") or {}).get("calculation_version") or ""
        ),
        "asset_return": block.get("return"),
        "benchmark_return": block.get("benchmark_return"),
        "relative_return": block.get("relative_return"),
    }


def iter_market_observations(fact: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for window in WINDOW_ORDER:
        row = market_observation(fact, window)
        if row is not None:
            rows.append(row)
    return rows


def _block(fact: Mapping[str, Any], window: str) -> Optional[Mapping[str, Any]]:
    windows = fact.get("windows") or {}
    block = windows.get(window)
    if not isinstance(block, Mapping):
        return None
    if block.get("return") is None or block.get("benchmark_return") is None:
        return None
    return block
