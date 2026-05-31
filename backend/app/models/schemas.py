from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class StockResponse(BaseModel):
    ticker: str
    current_price: Optional[float] = None
    market_cap: Optional[int] = None
    pe_ratio: Optional[float] = Field(None, description="Trailing P/E ratio")
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None


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
    reasons: List[str] = Field(default_factory=list)
    current_price: Optional[float] = None


class RecommendationsResponse(BaseModel):
    disclaimer: str
    generated_at: str
    horizon: str = "near-term"
    picks: List[RecommendationPick] = Field(default_factory=list)
    scanned_tickers: List[str] = Field(default_factory=list)
