"""Phase 11 extension — broker position sync request/response schemas."""

from typing import List, Optional

from pydantic import BaseModel, Field


class SyncedPosition(BaseModel):
    ticker: str
    shares: Optional[float] = None
    avg_cost: Optional[float] = None
    market_value: Optional[float] = None
    raw_line: str = ""


class PortfolioSyncOutput(BaseModel):
    positions: List[SyncedPosition] = Field(default_factory=list)
    broker_guess: str = "unknown"
    warnings: List[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "paste"


class PortfolioSyncPreviewRequest(BaseModel):
    raw_text: Optional[str] = Field(default=None, max_length=20000)
    use_openclaw: bool = False


class PortfolioSyncConfirmRequest(BaseModel):
    positions: List[SyncedPosition] = Field(default_factory=list)
