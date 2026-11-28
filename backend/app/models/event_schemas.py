"""Phase 2 — normalized market-event models (in-memory, not persisted)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    PRICE_MOVE = "PRICE_MOVE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    IDIOSYNCRATIC_MOVE = "IDIOSYNCRATIC_MOVE"
    EARNINGS = "EARNINGS"
    FILING = "FILING"
    NEWS = "NEWS"
    MACRO = "MACRO"


class EventSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EventDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"


class MarketEvent(BaseModel):
    """Normalized detector output. Not a database row."""

    event_id: str
    ticker: str
    event_type: EventType
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    occurred_at: Optional[datetime] = None
    direction: EventDirection = EventDirection.NONE
    magnitude: Optional[float] = None
    benchmark: Optional[str] = None
    z_score: Optional[float] = None
    residual_pct: Optional[float] = None
    portfolio_exposure: Optional[float] = None
    severity: EventSeverity = EventSeverity.MEDIUM
    source: str = "mcp"
    reason: str = ""
    detection_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self, *, bucket: str) -> str:
        return "|".join(
            [
                self.ticker.upper(),
                self.event_type.value,
                self.direction.value,
                bucket,
            ]
        )


class InvestigationMateriality(BaseModel):
    """Pre-investigation: is this event worth spending research resources?"""

    score: float
    should_investigate: bool
    reason: str
    depth: str = "standard"
    exposure: float = 0.0
    event_impact: float = 0.0
    primary_event_type: Optional[str] = None
    factors: Dict[str, float] = Field(default_factory=dict)


class InvestigationGateDecision(BaseModel):
    """Sweep / preview decision before the investigation runner starts."""

    ticker: str
    should_investigate: bool
    reason: str
    depth: str = "standard"
    score: float = 0.0
    exposure: float = 0.0
    cooldown: bool = False
    reopen: bool = False
    reopen_reason: str = ""
    prior_investigation_id: Optional[int] = None
    events: List[MarketEvent] = Field(default_factory=list)
    primary_event: Optional[MarketEvent] = None
    materiality: Optional[InvestigationMateriality] = None
    trigger_reason: str = "scheduled"
    window_label: str = "1d"
    move_pct: Optional[float] = None
    residual_pct: Optional[float] = None
    move_zscore: Optional[float] = None
    benchmark_move_pct: Optional[float] = None
    realized_vol_pct: Optional[float] = None
    asset_class: str = "equity"
    # Existing MoveSnapshot for the runner; not part of the public REST contract.
    move: Any = None

    model_config = {"arbitrary_types_allowed": True}
