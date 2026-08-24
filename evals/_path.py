"""Put backend/ on sys.path without touching production imports at module load."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def ensure_backend_on_path() -> None:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
