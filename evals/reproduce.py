"""Run identity: commit, dataset, model, timestamp."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from evals import BENCHMARK_VERSION, DATASET_VERSION
from evals._path import ROOT


def git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def model_label() -> str:
    name = (os.getenv("OPENAI_MODEL") or "").strip()
    if name:
        return name
    return "none (deterministic replay)"


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def run_metadata(configuration: dict) -> dict:
    return {
        "run_id": new_run_id(),
        "git_commit": git_commit(),
        "dataset_version": DATASET_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "model": model_label(),
        "timestamp": datetime.now(timezone.utc),
        "configuration": configuration,
    }
