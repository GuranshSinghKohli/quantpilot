import pytest
from pydantic import ValidationError

from app.core.settings import Settings
from app.pipeline import schemas
from app.pipeline.retry import RetryPolicy


def _settings(monkeypatch, **env) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


PINNED_BY_CONFTEST = (
    "LAKE_BACKEND", "LAKE_BUCKET", "LAKE_LOCAL_ROOT", "LAKE_ENDPOINT_URL", "KAFKA_BOOTSTRAP_SERVERS",
    "KAFKA_TOPIC_NEWS_RAW", "KAFKA_TOPIC_MARKET_RAW", "KAFKA_TOPIC_COMPANY_RAW", "KAFKA_TOPIC_DLQ",
    "KAFKA_CONSUMER_GROUP_LAKE",
)


def test_pipeline_defaults(monkeypatch):
    for pinned in PINNED_BY_CONFTEST:
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
    assert (s.KAFKA_BOOTSTRAP_SERVERS, s.KAFKA_CLIENT_ID, s.KAFKA_SECURITY_PROTOCOL) == (
        "localhost:9092", "quantpilot", "PLAINTEXT")
    assert (s.KAFKA_REPLICATION_FACTOR, s.KAFKA_RAW_RETENTION_HOURS, s.KAFKA_DLQ_RETENTION_HOURS) == (1, 168, 720)


def test_topic_names_are_configurable_and_validated(monkeypatch):
    assert _settings(monkeypatch, KAFKA_TOPIC_NEWS_RAW="dev.news.raw").raw_topics["news"] == "dev.news.raw"
    with pytest.raises(ValidationError, match="invalid Kafka"):
        _settings(monkeypatch, KAFKA_TOPIC_NEWS_RAW="bad topic!")
    with pytest.raises(ValidationError, match="distinct"):
        _settings(monkeypatch, KAFKA_TOPIC_NEWS_RAW="q.dlq", KAFKA_TOPIC_DLQ="q.dlq")


@pytest.mark.parametrize("value", ["localhost", "host:port", ":9092", "a:1;b:2", ""])
def test_bootstrap_servers_are_validated(monkeypatch, value):
    with pytest.raises(ValidationError, match="KAFKA_BOOTSTRAP_SERVERS"):
        _settings(monkeypatch, KAFKA_BOOTSTRAP_SERVERS=value)


def test_sasl_requires_credentials_and_keeps_password_secret(monkeypatch):
    from app.pipeline.kafka.config import client_config, producer_config

    with pytest.raises(ValidationError, match="SASL"):
        _settings(monkeypatch, KAFKA_SECURITY_PROTOCOL="SASL_SSL")
    s = _settings(monkeypatch, KAFKA_SECURITY_PROTOCOL="SASL_SSL", KAFKA_SASL_USERNAME="u",
                  KAFKA_SASL_PASSWORD="placeholder-not-a-secret")
    assert "placeholder-not-a-secret" not in repr(s)
    conf = client_config(s)
    assert (conf["security.protocol"], conf["sasl.username"], conf["sasl.password"]) == (
        "SASL_SSL", "u", "placeholder-not-a-secret")
    assert "sasl.password" not in client_config(_settings(monkeypatch, KAFKA_SECURITY_PROTOCOL="SSL"))
    prod = producer_config(s)
    assert prod["enable.idempotence"] is True and prod["acks"] == "all"


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
