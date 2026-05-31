import os
from typing import Any, Dict, List

import httpx

from mcp_server.tools.utils import ticker_to_cik, validate_ticker

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def _sec_headers() -> Dict[str, str]:
    user_agent = os.getenv(
        "SEC_EDGAR_USER_AGENT",
        "QuantPilot/1.0 (contact@example.com)",
    )
    return {"User-Agent": user_agent, "Accept": "application/json"}


def _build_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    cik_numeric = str(int(cik))
    accession_path = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_numeric}/{accession_path}/{primary_document}"
    )


def get_recent_filings(ticker: str, limit: int = 3) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    cik, company_name = ticker_to_cik(symbol)
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            SEC_SUBMISSIONS_URL.format(cik=cik), headers=_sec_headers()
        )
        response.raise_for_status()
        data = response.json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    filings: List[Dict[str, Any]] = []
    count = min(max(1, limit), len(forms))
    for i in range(count):
        accession = accession_numbers[i]
        primary_doc = primary_documents[i] if i < len(primary_documents) else ""
        filings.append(
            {
                "form_type": forms[i],
                "filing_date": filing_dates[i],
                "report_date": report_dates[i] if i < len(report_dates) else None,
                "accession_number": accession,
                "document_url": _build_document_url(cik, accession, primary_doc),
            }
        )

    return {
        "ticker": symbol,
        "cik": cik,
        "company_name": company_name or data.get("name"),
        "filings": filings,
    }


def get_company_facts(ticker: str) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    cik, company_name = ticker_to_cik(symbol)
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            SEC_SUBMISSIONS_URL.format(cik=cik), headers=_sec_headers()
        )
        response.raise_for_status()
        data = response.json()

    return {
        "ticker": symbol,
        "cik": cik,
        "company_name": company_name or data.get("name", ""),
        "sic": data.get("sic", ""),
        "sic_description": data.get("sicDescription", ""),
        "state_of_incorporation": data.get("stateOfIncorporation", ""),
    }
