"""League config defaults match docs/QUANTPILOT_LEAGUE.md sections 9 and 15."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.league.config import RISK_LIMITS, LeagueSettings

_SECTION_15 = (
    "DATA_DELAY_MINUTES",
    "UNIVERSE_PATH",
    "DATABASE_URL",
    "REDIS_URL",
    "KAFKA_BOOTSTRAP",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "LLM_PROVIDER",
    "LLM_FAST_MODEL",
    "LLM_STRONG_MODEL",
    "LLM_API_KEY",
    "AGENT_DAILY_TOKEN_BUDGET",
    "STARTING_CASH",
    "SEASON_WEEKS",
    "SLIPPAGE_BPS",
    "COMMISSION_USD",
    "FILL_TIMEOUT_MINUTES",
    "EVENT_IMPACT_THRESHOLD",
    "MAX_EVENT_CYCLES_PER_DAY",
    "MIN_ORDER_USD",
)

# Section 9 table, written out here so a silent edit of RISK_LIMITS fails this test.
_SECTION_9 = {
    "conservative": {
        "max_position_pct": 15,
        "max_positions": 10,
        "min_cash_buffer_pct": 20,
        "max_new_buys_per_day": 2,
        "max_single_order_pct": 10,
        "stop_loss_pct": -8,
        "etfs_allowed": True,
    },
    "balanced": {
        "max_position_pct": 25,
        "max_positions": 8,
        "min_cash_buffer_pct": 10,
        "max_new_buys_per_day": 4,
        "max_single_order_pct": 15,
        "stop_loss_pct": -12,
        "etfs_allowed": True,
    },
    "aggressive": {
        "max_position_pct": 40,
        "max_positions": 6,
        "min_cash_buffer_pct": 0,
        "max_new_buys_per_day": 6,
        "max_single_order_pct": 25,
        "stop_loss_pct": -20,
        "etfs_allowed": True,
    },
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SECTION_15:
        monkeypatch.delenv(name, raising=False)


def test_section_15_defaults(clean_env: None) -> None:
    settings = LeagueSettings()
    assert settings.DATA_DELAY_MINUTES == 0
    assert settings.UNIVERSE_PATH == "config/universe.csv"
    assert settings.DATABASE_URL == ""
    assert settings.REDIS_URL == ""
    assert settings.KAFKA_BOOTSTRAP == "localhost:9092"
    assert settings.NEO4J_URI == ""
    assert settings.NEO4J_USER == ""
    assert settings.NEO4J_PASSWORD == ""
    assert settings.LLM_PROVIDER == ""
    assert settings.LLM_FAST_MODEL == ""
    assert settings.LLM_STRONG_MODEL == ""
    assert settings.LLM_API_KEY == ""
    assert settings.AGENT_DAILY_TOKEN_BUDGET == 60_000
    assert settings.STARTING_CASH == Decimal("1000")
    assert settings.SEASON_WEEKS == 4
    assert settings.SLIPPAGE_BPS == 5
    assert settings.COMMISSION_USD == Decimal("0")
    assert settings.FILL_TIMEOUT_MINUTES == 30
    assert settings.EVENT_IMPACT_THRESHOLD == Decimal("0.7")
    assert settings.MAX_EVENT_CYCLES_PER_DAY == 3
    assert settings.MIN_ORDER_USD == Decimal("5")


def test_environment_overrides_a_default(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DELAY_MINUTES", "15")
    assert LeagueSettings().DATA_DELAY_MINUTES == 15


def test_risk_table_matches_section_9() -> None:
    assert RISK_LIMITS == _SECTION_9
    assert set(RISK_LIMITS) == {"conservative", "balanced", "aggressive"}
