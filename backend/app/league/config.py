"""League settings. Every default is the value in docs/QUANTPILOT_LEAGUE.md section 15 or 9.

The risk table is one dict so a later change touches one place. It is not read
from the environment. Section 15 does not name those keys.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Literal, Union

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RiskValue = Union[int, bool]
RiskLevel = Literal["conservative", "balanced", "aggressive"]

# Section 9, one row per risk level. Percents are the table's numbers, not fractions.
# stop_loss_pct is negative: −8 is the conservative auto-sell.
RISK_LIMITS: Dict[str, Dict[str, RiskValue]] = {
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


class LeagueSettings(BaseSettings):
    """Section 15. Init arguments override the environment, which overrides these defaults."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    ALPACA_API_KEY: str = ""
    ALPACA_API_SECRET: str = ""
    ALPACA_DATA_FEED: Literal["iex", "sip"] = "iex"
    DATA_DELAY_MINUTES: int = 0
    UNIVERSE_PATH: str = "config/universe.csv"

    DATABASE_URL: str = ""
    REDIS_URL: str = ""
    KAFKA_BOOTSTRAP: str = "localhost:9092"
    NEO4J_URI: str = ""
    NEO4J_USER: str = ""
    NEO4J_PASSWORD: str = ""

    LLM_PROVIDER: str = ""
    LLM_FAST_MODEL: str = ""
    LLM_STRONG_MODEL: str = ""
    LLM_API_KEY: str = ""
    AGENT_DAILY_TOKEN_BUDGET: int = 60_000

    STARTING_CASH: Decimal = Field(default_factory=lambda: Decimal("1000"))
    SEASON_WEEKS: int = 4
    SLIPPAGE_BPS: int = 5
    COMMISSION_USD: Decimal = Field(default_factory=lambda: Decimal("0"))
    FILL_TIMEOUT_MINUTES: int = 30
    EVENT_IMPACT_THRESHOLD: Decimal = Field(default_factory=lambda: Decimal("0.7"))
    MAX_EVENT_CYCLES_PER_DAY: int = 3
    MIN_ORDER_USD: Decimal = Field(default_factory=lambda: Decimal("5"))


def risk_limits() -> Dict[str, Dict[str, RiskValue]]:
    """Return the single section 9 table. Callers do not copy the numbers elsewhere."""
    return RISK_LIMITS
