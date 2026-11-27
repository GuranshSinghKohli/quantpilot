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

from hypothesis import settings as hypothesis_settings

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

# And for Kafka: an unroutable broker and test-only names. The kafka tier
# (tests/kafka/) builds its own Settings with per-test topics and groups.
os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "127.0.0.1:9"
os.environ["KAFKA_TOPIC_NEWS_RAW"] = "test.unconfigured.news.raw"
os.environ["KAFKA_TOPIC_MARKET_RAW"] = "test.unconfigured.market.raw"
os.environ["KAFKA_TOPIC_COMPANY_RAW"] = "test.unconfigured.company.raw"
os.environ["KAFKA_TOPIC_DLQ"] = "test.unconfigured.ingestion.dlq"
os.environ["KAFKA_CONSUMER_GROUP_LAKE"] = "test.unconfigured.lake-writer"
os.environ["KAFKA_TOPIC_RESEARCH_REQUEST"] = "test.unconfigured.research.request"
os.environ["KAFKA_TOPIC_RESEARCH_COMPLETED"] = "test.unconfigured.research.completed"
os.environ["KAFKA_TOPIC_RESEARCH_DLQ"] = "test.unconfigured.research.dlq"
os.environ["KAFKA_CONSUMER_GROUP_RESEARCH"] = "test.unconfigured.research-worker"

# And for external sources: no keys and an unroutable host, so nothing under test
# can reach a real provider even if .env holds credentials. Adapter tests inject
# an httpx MockTransport.
os.environ["NEWS_API_KEY"] = ""
os.environ["MARKET_API_KEY"] = ""
for _var in ("NEWS_BASE_URL", "MARKET_BASE_URL", "COMPANY_BASE_URL"):
    os.environ[_var] = "http://127.0.0.1:9"

os.environ.setdefault("INVESTIGATIONS_SWEEP_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["OPENAI_API_KEY"] = ""

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Loaded before test modules, so @settings() on a test inherits this profile.
# The default stays at 20 examples. HYPOTHESIS_PROFILE=thorough selects 300.
hypothesis_settings.register_profile("default", max_examples=20)
hypothesis_settings.register_profile("thorough", max_examples=300)
hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))
