"""Latency percentiles. Measure only; do not optimize here."""

from __future__ import annotations

from typing import Sequence


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def latency_metrics(totals: Sequence[float]) -> dict:
    return {
        "latency_p50_ms": percentile(totals, 50),
        "latency_p95_ms": percentile(totals, 95),
        "latency_p99_ms": percentile(totals, 99),
        "latency_mean_ms": sum(totals) / len(totals) if totals else 0.0,
    }
