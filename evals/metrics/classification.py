"""Precision / recall / F1. Deterministic."""

from __future__ import annotations

from typing import Sequence


def precision(tp: int, fp: int) -> float:
    denom = tp + fp
    return float(tp) / denom if denom else 0.0


def recall(tp: int, fn: int) -> float:
    denom = tp + fn
    return float(tp) / denom if denom else 0.0


def f1(prec: float, rec: float) -> float:
    if prec + rec == 0:
        return 0.0
    return 2.0 * prec * rec / (prec + rec)


def binary_prf(y_true: Sequence[bool], y_pred: Sequence[bool]) -> dict:
    tp = fp = fn = 0
    for truth, pred in zip(y_true, y_pred):
        if truth and pred:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif truth and not pred:
            fn += 1
    p = precision(tp, fp)
    r = recall(tp, fn)
    return {
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
        "precision": p,
        "recall": r,
        "f1": f1(p, r),
    }
