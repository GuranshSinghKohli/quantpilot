"""Alert schemas (Phase 9)."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator

AlertType = Literal["price_above", "price_below", "volatility_pct", "news_sentiment"]


class AlertRuleCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    alert_type: AlertType
    threshold: float
    cooldown_minutes: int = Field(default=60, ge=5, le=24 * 60)
    note: str = Field(default="", max_length=280)
    enabled: bool = True

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("Ticker is required.")
        return symbol

    @field_validator("threshold")
    @classmethod
    def validate_threshold_range(cls, value: float, info: ValidationInfo) -> float:
        alert_type = info.data.get("alert_type")
        if alert_type == "news_sentiment":
            if value == 0 or abs(value) > 1:
                raise ValueError(
                    "News sentiment threshold must be between -1 and 1 (not zero). "
                    "Use e.g. 0.3 for bullish or -0.3 for bearish."
                )
        elif alert_type == "volatility_pct":
            if value <= 0 or value > 100:
                raise ValueError("Volatility threshold must be between 0 and 100 (%).")
        elif alert_type in ("price_above", "price_below"):
            if value <= 0:
                raise ValueError("Price threshold must be greater than 0.")
        return value


class AlertRuleUpdate(BaseModel):
    threshold: Optional[float] = None
    cooldown_minutes: Optional[int] = Field(default=None, ge=5, le=24 * 60)
    note: Optional[str] = Field(default=None, max_length=280)
    enabled: Optional[bool] = None


class AlertRuleOut(BaseModel):
    id: int
    ticker: str
    alert_type: str
    threshold: float
    enabled: bool
    cooldown_minutes: int
    last_triggered_at: Optional[str] = None
    created_at: str
    note: str = ""

    model_config = {"from_attributes": True}


class AlertEventOut(BaseModel):
    id: int
    rule_id: Optional[int] = None
    ticker: str
    alert_type: str
    title: str
    message: str
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    created_at: str
    read_at: Optional[str] = None
    is_read: bool = False


class AlertEvaluateResponse(BaseModel):
    evaluated_rules: int
    triggered: int
    redis_mode: str
    events: List[AlertEventOut] = Field(default_factory=list)
