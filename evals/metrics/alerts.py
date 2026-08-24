"""Alert precision helpers."""

from __future__ import annotations

from evals.metrics.classification import binary_prf


def alert_metrics(y_true: list, y_pred: list) -> dict:
    raw = binary_prf(y_true, y_pred)
    return {
        "alert_precision": raw["precision"],
        "alert_recall": raw["recall"],
        "alert_f1": raw["f1"],
    }
