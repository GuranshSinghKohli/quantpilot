from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def _settings(monkeypatch, **env) -> Settings:
    for key in (
        "DATABASE_URL",
        "ENVIRONMENT",
        "DB_AUTO_MIGRATE",
        "SENTRY_ENVIRONMENT",
        "JWT_SECRET_KEY",
        "OPENAI_API_KEY",
        "KAFKA_BOOTSTRAP_SERVERS",
        "LAKE_BACKEND",
        "LAKE_ENDPOINT_URL",
        "ALLOWED_ORIGINS",
        "LLM_PROVIDER",
        "EMBEDDING_PROVIDER",
        "ALLOW_HTTP_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_defaults_are_local_and_safe(monkeypatch):
    s = _settings(monkeypatch)
    assert s.is_sqlite
    assert s.ENVIRONMENT == "development"
    assert s.auto_migrate is True
    assert s.INVESTIGATIONS_SWEEP_ENABLED is False


def test_hosted_postgres_scheme_is_normalized(monkeypatch):
    s = _settings(monkeypatch, DATABASE_URL="postgres://u:p@db:5432/qp")
    assert s.DATABASE_URL == "postgresql://u:p@db:5432/qp"
    assert not s.is_sqlite


@pytest.mark.parametrize("raw,expected", [("1", True), ("YES", True), ("on", True), ("0", False), ("", False), ("nope", False)])
def test_flags_accept_common_spellings(monkeypatch, raw, expected):
    assert _settings(monkeypatch, RATE_LIMIT_ENABLED=raw).RATE_LIMIT_ENABLED is expected


def test_auto_migrate_is_off_outside_development_unless_forced(monkeypatch):
    assert _settings(monkeypatch, ENVIRONMENT="Production").auto_migrate is False
    assert _settings(monkeypatch, ENVIRONMENT="production", DB_AUTO_MIGRATE="true").auto_migrate is True


def test_invalid_values_fail_fast(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, JWT_EXPIRE_DAYS="0")
    with pytest.raises(ValidationError):
        _settings(monkeypatch, INVESTIGATION_NOTIFY_MIN_DEPTH="extreme")


def test_secrets_are_not_printed(monkeypatch):
    s = _settings(monkeypatch, JWT_SECRET_KEY="super-secret-value")
    assert "super-secret-value" not in repr(s)
    assert s.JWT_SECRET_KEY.get_secret_value() == "super-secret-value"


def _production(**extra: str) -> dict:
    values = {
        "ENVIRONMENT": "production",
        "JWT_SECRET_KEY": "x" * 40,
        "DATABASE_URL": "postgresql://quantpilot:secret@qp.internal:5432/quantpilot",
        "OPENAI_API_KEY": "sk-example",
        "KAFKA_BOOTSTRAP_SERVERS": "kafka.internal:9092",
        "LAKE_BACKEND": "s3",
        "DB_AUTO_MIGRATE": "false",
        "ALLOWED_ORIGINS": "https://app.example.com",
        "LLM_PROVIDER": "openai",
        "EMBEDDING_PROVIDER": "openai",
    }
    values.update(extra)
    return values


def test_production_refuses_dev_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        _settings(monkeypatch, **_production(JWT_SECRET_KEY="dev-only-change-me-quantpilot")).validate_for_environment()
    _settings(monkeypatch, **_production()).validate_for_environment()


@pytest.mark.parametrize(
    "override,match",
    [
        ({"DATABASE_URL": "sqlite:////tmp/quantpilot.db"}, "PostgreSQL"),
        ({"DATABASE_URL": "postgresql://quantpilot:secret@localhost:5432/quantpilot"}, "localhost"),
        ({"LLM_PROVIDER": "fake"}, "fake"),
        ({"EMBEDDING_PROVIDER": "fake"}, "fake"),
        ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY"),
        ({"KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:9092"}, "localhost"),
        ({"LAKE_BACKEND": "local"}, "LAKE_BACKEND"),
        ({"LAKE_ENDPOINT_URL": "http://minio:9000"}, "LAKE_ENDPOINT_URL"),
        ({"DB_AUTO_MIGRATE": "true"}, "DB_AUTO_MIGRATE"),
        ({"ALLOWED_ORIGINS": "http://localhost:3000"}, "ALLOWED_ORIGINS"),
        ({"ALLOWED_ORIGINS": "http://staging.example.com"}, "https"),
    ],
)
def test_production_refuses_development_defaults(monkeypatch, override, match):
    with pytest.raises(ValueError, match=match):
        _settings(monkeypatch, **_production(**override)).validate_for_environment()


def test_staging_http_origin_requires_explicit_flag(monkeypatch):
    with pytest.raises(ValueError, match="local"):
        _settings(
            monkeypatch,
            **_production(ALLOWED_ORIGINS="http://127.0.0.1:3000", ALLOW_HTTP_ORIGINS="true"),
        ).validate_for_environment()
    _settings(
        monkeypatch,
        **_production(ALLOWED_ORIGINS="http://quantpilot-staging.example.com", ALLOW_HTTP_ORIGINS="true"),
    ).validate_for_environment()
