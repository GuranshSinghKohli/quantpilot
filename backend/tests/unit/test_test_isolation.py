from app.core.db import engine
from app.core.settings import get_settings
from app.pipeline.lake import build_object_store


def test_suite_never_binds_to_developer_database():
    assert engine.url.get_backend_name() == "sqlite"
    assert "/tests/_test_" in str(engine.url.database)


def test_suite_never_binds_to_developer_lake():
    settings = get_settings()
    assert settings.LAKE_BACKEND == "local"
    assert settings.LAKE_BUCKET.startswith("quantpilot-test-")
    store = build_object_store()
    assert "/tests/_test_lake" in store.root.as_posix()


def test_suite_never_binds_to_developer_kafka():
    settings = get_settings()
    assert settings.KAFKA_BOOTSTRAP_SERVERS == "127.0.0.1:9"
    names = [*settings.raw_topics.values(), settings.KAFKA_TOPIC_DLQ, settings.KAFKA_CONSUMER_GROUP_LAKE]
    assert all(name.startswith("test.") for name in names)


def test_suite_never_reaches_real_source_providers():
    settings = get_settings()
    assert not settings.NEWS_API_KEY.get_secret_value()
    assert not settings.MARKET_API_KEY.get_secret_value()
    for url in (settings.NEWS_BASE_URL, settings.MARKET_BASE_URL, settings.COMPANY_BASE_URL):
        assert url == "http://127.0.0.1:9"
