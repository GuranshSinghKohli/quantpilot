"""SEC company/ticker/exchange reference data.

``https://www.sec.gov/files/company_tickers_exchange.json`` is one keyless
document: ``{"fields": ["cik","name","ticker","exchange"], "data": [[...], ...]}``
with one row per ticker, so a CIK with several share classes has several rows.
SEC fair-access policy requires a User-Agent with contact details and at most
10 requests/second.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterator, List

from pydantic import ValidationError

from app.pipeline.contracts import SOURCES, CompanyRecord
from app.pipeline.sources.base import (
    HealthReport,
    IngestRequest,
    NormalizationError,
    Page,
    SourceAdapter,
    SourceItem,
    Stream,
    probe_health,
    validation_details,
)
from app.pipeline.sources.http import PermanentSourceError, SourceClient, SourceFailure

PROVIDER = "sec"
PATH = "/files/company_tickers_exchange.json"
FIELDS = ("cik", "name", "ticker", "exchange")


class SecCompanyAdapter(SourceAdapter):
    spec = SOURCES["sec.company_tickers"]
    provider = PROVIDER

    def __init__(self, client: SourceClient) -> None:
        self.client = client

    def streams(self, request: IngestRequest) -> List[Stream]:
        # One document, no pagination; the window does not apply to reference data.
        return [Stream(label="company_tickers_exchange", pages=lambda: self._pages(request))]

    def _pages(self, request: IngestRequest) -> Iterator[Page]:
        body, _ = self.client.get_json(PATH, operation="company_tickers_exchange")
        yield Page(group_by_cik(body, set(request.symbols), provider=self.client.provider))

    def normalize(self, item: SourceItem, *, ingested_at: datetime) -> CompanyRecord:
        fields, rows = item.original.get("fields"), item.original.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list) or not rows:
            raise NormalizationError("item has no rows", [{"field": "data", "error": "empty"}])
        index = {name: fields.index(name) for name in FIELDS}
        bad = [i for i, row in enumerate(rows) if not _well_formed(row, len(fields))]
        if bad:
            raise NormalizationError("malformed SEC row", [{"field": f"data[{i}]", "error": "not a row"} for i in bad])
        first = rows[0]
        tickers = [row[index["ticker"]] for row in rows if row[index["ticker"]]]
        try:
            return CompanyRecord.create(
                source=self.source,
                external_id=first[index["cik"]],
                cik=first[index["cik"]],
                name=first[index["name"]],
                tickers=tickers,
                ticker=tickers[0] if tickers else None,
                exchange=first[index["exchange"]] or None,
                ingested_at=ingested_at,
            )
        except ValidationError as exc:
            raise NormalizationError(f"CIK {item.ref} failed the company contract", validation_details(exc)) from exc

    def health(self) -> HealthReport:
        report = HealthReport(self.source, self.provider, configured=True)
        return probe_health(report, self.client, PATH, method="HEAD", auth_required=False)


def _well_formed(row: Any, width: int) -> bool:
    return isinstance(row, list) and len(row) == width and isinstance(row[0], int) and not isinstance(row[0], bool)


def group_by_cik(body: Any, symbols: set, *, provider: str) -> List[SourceItem]:
    """One item per CIK with all of its rows verbatim, in document order. Grouping
    is by CIK only; equal names never merge. A row that cannot be grouped becomes
    its own item so normalization rejects it visibly."""
    if not isinstance(body, dict) or not isinstance(body.get("fields"), list) or not isinstance(body.get("data"), list):
        raise PermanentSourceError(SourceFailure.BAD_RESPONSE, "expected {fields: [...], data: [...]}", provider=provider)
    fields = body["fields"]
    if not all(name in fields for name in FIELDS):
        raise PermanentSourceError(SourceFailure.BAD_RESPONSE, f"fields {fields} lack {list(FIELDS)}", provider=provider)
    cik_at, ticker_at = fields.index("cik"), fields.index("ticker")
    wanted = {s.upper() for s in symbols}
    groups: Dict[int, List[list]] = {}
    items: List[SourceItem] = []
    for position, row in enumerate(body["data"]):
        if not _well_formed(row, len(fields)):
            # Out of scope only if it provably names none of the wanted symbols.
            values = row if isinstance(row, list) else [row]
            if not wanted or any(str(v).upper() in wanted for v in values):
                items.append(SourceItem(original={"fields": fields, "data": [row]}, ref=f"row:{position}"))
            continue
        groups.setdefault(row[cik_at], []).append(row)
    for cik, rows in groups.items():
        if wanted and not any(str(r[ticker_at] or "").upper() in wanted for r in rows):
            continue
        items.append(SourceItem(original={"fields": fields, "data": rows}, ref=f"cik:{cik}"))
    return items
