from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def _settings(monkeypatch, **env) -> Settings:
    for key in ("DATABASE_URL", "ENVIRONMENT", "DB_AUTO_MIGRATE", "SENTRY_ENVIRONMENT"):
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


def test_production_refuses_dev_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValueError):
        _settings(monkeypatch, ENVIRONMENT="production").validate_for_environment()
    _settings(monkeypatch, ENVIRONMENT="production", JWT_SECRET_KEY="x" * 40).validate_for_environment()
