"""Ablation labels. Production defaults are unchanged."""

from __future__ import annotations

ABLATIONS = ("full", "no_da", "no_memory", "no_browser", "no_verification", "baseline")


def describe(name: str) -> str:
    return {
        "full": "QuantPilot multi-stage (verify + DA + memory + cutoff)",
        "no_da": "Without Devil's Advocate",
        "no_memory": "Without company memory retrieve",
        "no_browser": "Without IR/browser evidence",
        "no_verification": "Without verification / final report",
        "baseline": "Single-agent: first snippet is the cause; no cutoff",
    }.get(name, name)
