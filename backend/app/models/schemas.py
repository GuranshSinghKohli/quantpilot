from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    # PRD v3 Phase 13 — optional observability flags (always present, usually false locally)
    langsmith: bool = False
    sentry: bool = False
    otel: bool = False
    version: str = "1.0.0"
    application: str = "ok"
    database: str = "unknown"
    redis: str = "unknown"
    draining: bool = False


class ReadyResponse(BaseModel):
    status: str
    application: str
    database: str
    redis: str
    draining: bool = False
    request_id: str = ""


class StockResponse(BaseModel):
    ticker: str
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    market_cap: Optional[int] = None
    pe_ratio: Optional[float] = Field(None, description="Trailing P/E ratio")
    forward_pe: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    volume: Optional[int] = None
    avg_volume: Optional[int] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None


class FilingResponse(BaseModel):
    form_type: str
    filing_date: str
    report_date: Optional[str] = None
    accession_number: str
    document_url: str


class FilingsListResponse(BaseModel):
    ticker: str
    cik: str
    company_name: Optional[str] = None
    filings: List[FilingResponse]


class RecommendationPick(BaseModel):
    ticker: str
    score: float = Field(ge=0.0, le=1.0, description="Higher = more constructive near-term setup")
    outlook: Literal["bullish", "neutral", "bearish"] = "neutral"
    near_term_view: str = ""
    catalyst_hint: str = ""
    reasons: List[str] = Field(default_factory=list)
    current_price: Optional[float] = None


class RecommendationsResponse(BaseModel):
    disclaimer: str
    generated_at: str
    horizon: str = "near-term"
    picks: List[RecommendationPick] = Field(default_factory=list)
    scanned_tickers: List[str] = Field(default_factory=list)
