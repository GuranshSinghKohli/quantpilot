from typing import List, Optional

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
