"""The league migration guard never opens a connection."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.league.local_url import LeagueDatabaseRefused, assert_localhost_database_url

INI = Path(__file__).resolve().parents[3] / "alembic_league.ini"


def test_localhost_urls_pass() -> None:
    for url in (
        "postgresql://quantpilot:quantpilot@localhost:5432/quantpilot",
        "postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot",
        "postgresql://quantpilot:quantpilot@[::1]:5432/quantpilot",
    ):
        assert assert_localhost_database_url(url) == url


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


def test_alembic_refuses_an_rds_url_before_connecting() -> None:
    cfg = Config(str(INI))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = (
        "postgresql://u:p@quantpilot.abc.us-east-1.rds.amazonaws.com:5432/quantpilot"
    )
    with pytest.raises(LeagueDatabaseRefused):
        command.upgrade(cfg, "head")
