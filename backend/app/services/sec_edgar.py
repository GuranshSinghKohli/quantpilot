import os
from typing import List, Optional, Tuple

import httpx

from app.models.schemas import FilingResponse, FilingsListResponse

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def _headers() -> dict[str, str]:
    user_agent = os.getenv(
        "SEC_EDGAR_USER_AGENT",
        "QuantPilot/1.0 (contact@example.com)",
    )
    return {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }


async def _get_cik_for_ticker(
    client: httpx.AsyncClient, ticker: str
) -> Tuple[str, Optional[str]]:
    response = await client.get(SEC_TICKERS_URL, headers=_headers())
    response.raise_for_status()
    data = response.json()

    target = ticker.upper().strip()
    for entry in data.values():
        if entry.get("ticker", "").upper() == target:
            cik = str(entry["cik_str"]).zfill(10)
            return cik, entry.get("title")
    raise ValueError(f"No SEC CIK found for ticker '{target}'")


def _build_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    cik_numeric = str(int(cik))
    accession_path = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_numeric}/{accession_path}/{primary_document}"
    )


async def get_recent_filings(ticker: str, limit: int = 3) -> FilingsListResponse:
    async with httpx.AsyncClient(timeout=30.0) as client:
        cik, company_name = await _get_cik_for_ticker(client, ticker)

        submissions_url = SEC_SUBMISSIONS_URL.format(cik=cik)
        response = await client.get(submissions_url, headers=_headers())
        response.raise_for_status()
        data = response.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])

        if not forms:
            raise ValueError(f"No filings found for ticker '{ticker.upper()}'")

        filings: List[FilingResponse] = []
        count = min(limit, len(forms))

        for i in range(count):
            accession = accession_numbers[i]
            primary_doc = primary_documents[i] if i < len(primary_documents) else ""
            filings.append(
                FilingResponse(
                    form_type=forms[i],
                    filing_date=filing_dates[i],
                    report_date=report_dates[i] if i < len(report_dates) and report_dates[i] else None,
                    accession_number=accession,
                    document_url=_build_document_url(cik, accession, primary_doc),
                )
            )

        return FilingsListResponse(
            ticker=ticker.upper().strip(),
            cik=cik,
            company_name=company_name or data.get("name"),
            filings=filings,
        )
