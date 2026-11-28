"""The league migration guard never opens a connection."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.league.local_url import (
    LeagueDatabaseRefused,
    assert_league_database,
    assert_localhost_database_url,
)

INI = Path(__file__).resolve().parents[3] / "alembic_league.ini"


def test_localhost_urls_pass() -> None:
    for url in (
        "postgresql://quantpilot:quantpilot@localhost:5432/quantpilot",
        "postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot",
        "postgresql://quantpilot:quantpilot@[::1]:5432/quantpilot",
        "postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot_test",
        "postgresql://quantpilot:quantpilot@localhost:5433/quantpilot_test",
    ):
        assert assert_localhost_database_url(url) == url


@pytest.mark.parametrize("name", ["roadsafety", "quantpilot_other", "postgres"])
def test_localhost_database_outside_the_allowed_set_is_refused(name: str) -> None:
    url = f"postgresql://quantpilot:quantpilot@127.0.0.1:5433/{name}"
    with pytest.raises(LeagueDatabaseRefused, match=name):
        assert_localhost_database_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///tmp/quantpilot.db",
        "postgresql://quantpilot:quantpilot@postgres:5432/quantpilot",
        "postgresql://u:p@quantpilot.abc.us-east-1.rds.amazonaws.com:5432/quantpilot",
        "postgresql://u:p@example.com:5432/quantpilot",
    ],
)
def test_non_local_urls_are_refused(url: str) -> None:
    with pytest.raises(LeagueDatabaseRefused):
        assert_localhost_database_url(url)


class _Scalar:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class _Conn:
    """Returns a database name, then the public.users probe. No network."""

    def __init__(self, name: str, users: object) -> None:
        self._answers = [_Scalar(name), _Scalar(users)]

    def execute(self, _statement: object) -> _Scalar:
        return self._answers.pop(0)


def test_quantpilot_without_public_users_is_refused() -> None:
    with pytest.raises(LeagueDatabaseRefused, match="public.users"):
        assert_league_database(_Conn("quantpilot", None))


def test_quantpilot_test_without_public_users_is_refused() -> None:
    with pytest.raises(LeagueDatabaseRefused, match="public.users"):
        assert_league_database(_Conn("quantpilot_test", None))


def test_quantpilot_test_with_public_users_is_accepted() -> None:
    assert_league_database(_Conn("quantpilot_test", 1)) is None


def test_connected_database_outside_the_allowed_set_is_refused() -> None:
    with pytest.raises(LeagueDatabaseRefused, match="roadsafety"):
        assert_league_database(_Conn("roadsafety", 1))


def test_alembic_refuses_an_rds_url_before_connecting() -> None:
    cfg = Config(str(INI))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = (
        "postgresql://u:p@quantpilot.abc.us-east-1.rds.amazonaws.com:5432/quantpilot"
    )
    with pytest.raises(LeagueDatabaseRefused):
        command.upgrade(cfg, "head")
