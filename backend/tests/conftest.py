"""Test process setup: import path, quiet background jobs, isolated database.

This file is loaded before any test module imports ``app``. The engine is built
once per process from the first DATABASE_URL it sees, so the URL must point at
a throwaway file here — otherwise it falls through to ``.env`` and the tests
write into the developer's database.
"""

import os
import shutil
import sys
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_session.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"

# Same rule for the lake: nothing under test may reach backend/data/lake or the
# real bucket. Tests that need S3 build their own store (see tests/s3/).
_TEST_LAKE = Path(__file__).resolve().parent / "_test_lake"
if _TEST_LAKE.exists():
    shutil.rmtree(_TEST_LAKE)
os.environ["LAKE_BACKEND"] = "local"
os.environ["LAKE_LOCAL_ROOT"] = _TEST_LAKE.as_posix()
os.environ["LAKE_BUCKET"] = "quantpilot-test-unconfigured"
os.environ["LAKE_ENDPOINT_URL"] = ""

os.environ.setdefault("INVESTIGATIONS_SWEEP_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
