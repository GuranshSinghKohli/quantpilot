import pytest
from pydantic import ValidationError

from app.core.settings import Settings
from app.pipeline import schemas
from app.pipeline.retry import RetryPolicy


def _settings(monkeypatch, **env) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_pipeline_defaults(monkeypatch):
    for pinned in ("LAKE_BACKEND", "LAKE_BUCKET", "LAKE_LOCAL_ROOT", "LAKE_ENDPOINT_URL"):
        monkeypatch.delenv(pinned, raising=False)
    s = _settings(monkeypatch)
    assert s.raw_topics == {
        "news": "quantpilot.news.raw",
        "market": "quantpilot.market.raw",
        "company": "quantpilot.company.raw",
    }
    assert s.KAFKA_TOPIC_DLQ == "quantpilot.ingestion.dlq"
    assert s.KAFKA_CONSUMER_GROUP_LAKE == "quantpilot.lake-writer"
    assert s.LAKE_BACKEND == "s3"
    assert s.LAKE_BUCKET == "quantpilot-lake"


def test_topic_names_are_configurable_and_validated(monkeypatch):
    assert _settings(monkeypatch, KAFKA_TOPIC_NEWS_RAW="dev.news.raw").raw_topics["news"] == "dev.news.raw"
    with pytest.raises(ValidationError, match="invalid Kafka"):
        _settings(monkeypatch, KAFKA_TOPIC_NEWS_RAW="bad topic!")
    with pytest.raises(ValidationError, match="distinct"):
        _settings(monkeypatch, KAFKA_TOPIC_NEWS_RAW="quantpilot.ingestion.dlq")


def test_ingest_symbols_are_parsed(monkeypatch):
    assert _settings(monkeypatch, INGEST_SYMBOLS=" aapl, msft ,,nvda ").ingest_symbols == ["AAPL", "MSFT", "NVDA"]


def test_retry_policy_comes_from_settings(monkeypatch):
    s = _settings(monkeypatch, PIPELINE_RETRY_MAX_ATTEMPTS="3", PIPELINE_RETRY_BASE_SECONDS="0.1")
    policy = RetryPolicy.from_settings(s)
    assert (policy.max_attempts, policy.base_delay) == (3, 0.1)
    with pytest.raises(ValidationError, match="PIPELINE_RETRY_MAX_SECONDS"):
        _settings(monkeypatch, PIPELINE_RETRY_BASE_SECONDS="60", PIPELINE_RETRY_MAX_SECONDS="1")


def test_committed_json_schemas_match_the_contracts():
    assert schemas.drift() == [], "run: python -m app.pipeline schemas"
