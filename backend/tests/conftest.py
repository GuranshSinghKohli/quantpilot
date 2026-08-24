"""Ensure the monorepo root (containing mcp_server/) is importable in tests."""

import os
import sys
from pathlib import Path

# Phase 9 sweep defaults on in production. Keep test processes quiet.
os.environ.setdefault("INVESTIGATIONS_SWEEP_ENABLED", "false")
os.environ.setdefault("BRIEFING_ENABLED", "false")
os.environ.setdefault("ALERTS_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
