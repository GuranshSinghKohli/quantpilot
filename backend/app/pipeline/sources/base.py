"""The source adapter interface.

An adapter fetches provider data and normalizes it into contract records. It
does not publish, store, or deduplicate: ``app.pipeline.ingest.IngestionRunner``
turns its items into events and hands them to the existing publisher.

    adapter.streams(request) → Stream(label, pages()) → Page(items=[SourceItem])
    adapter.normalize(item, ingested_at) → NewsRecord | MarketRecord | CompanyRecord
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from pydantic import ValidationError

from app.core.structured_logging import get_logger, log_event
from app.pipeline.contracts import Record, SourceSpec
from app.pipeline.errors import FailureReason, PermanentError
from app.pipeline.sources.http import PermanentSourceError, SourceClient, SourceFailure

_log = get_logger("quantpilot.pipeline.sources")


@dataclass(frozen=True)
class IngestRequest:
    """One bounded fetch: an explicit window, a symbol universe, hard limits."""

    start: datetime
    end: datetime
    symbols: Tuple[str, ...] = ()
    max_records: int = 1000
    page_size: int = 100
    max_pages: int = 20

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if min(self.max_records, self.page_size, self.max_pages) < 1:
            raise ValueError("max_records, page_size and max_pages must be >= 1")

    def params(self) -> Dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "symbols": list(self.symbols),
            "max_records": self.max_records,
            "page_size": self.page_size,
            "max_pages": self.max_pages,
        }


@dataclass(frozen=True)
class SourceItem:
    """``original`` is the provider's object verbatim (the raw layer). ``ref`` is a
    short, non-secret handle for logs and dead letters."""

    original: Dict[str, Any]
    ref: str


@dataclass
class Page:
    items: List[SourceItem]
    has_more: bool = False


@dataclass
class Stream:
    """One independently failing unit of fetching (a ticker, a document)."""

    label: str
    pages: Callable[[], Iterator[Page]]


class NormalizationError(PermanentError):
    """A provider item that cannot become a valid record. It is dead-lettered with
    the verbatim item; never repaired, never dropped silently."""

    def __init__(self, message: str, details: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(FailureReason.PAYLOAD_INVALID, message, details)


def validation_details(exc: ValidationError) -> List[Dict[str, Any]]:
    """Field locations and messages only: provider values are not copied into errors."""
    return [{"field": ".".join(str(p) for p in e["loc"]), "error": e["msg"]}
            for e in exc.errors(include_url=False, include_input=False, include_context=False)]


def require(original: Dict[str, Any], *fields: str) -> None:
    missing = [name for name in fields if original.get(name) is None]
    if missing:
        raise NormalizationError(
            f"missing required fields: {', '.join(missing)}",
            [{"field": name, "error": "missing"} for name in missing],
        )


class SourceAdapter(ABC):
    spec: SourceSpec
    provider: str

    @property
    def source(self) -> str:
        return self.spec.key

    @abstractmethod
    def streams(self, request: IngestRequest) -> List[Stream]:
        ...

    @abstractmethod
    def normalize(self, item: SourceItem, *, ingested_at: datetime) -> Record:
        ...

    @abstractmethod
    def health(self) -> "HealthReport":
        """One unretried request; never part of the API's /health."""

    def close(self) -> None:
        client = getattr(self, "client", None)
        if isinstance(client, SourceClient):
            client.close()


def _fingerprint(cursor: str) -> str:
    return hashlib.sha256(cursor.encode()).hexdigest()[:12]


def follow_cursor(
    client: SourceClient,
    path: str,
    params: Dict[str, Any],
    *,
    operation: str,
    max_pages: int,
    label: str,
    items_of: Callable[[Any], List[SourceItem]],
    cursor_of: Callable[[Any], Optional[str]],
) -> Iterator[Page]:
    """Cursor pagination that cannot loop: a repeated cursor is a provider error,
    an empty page ends the stream, and ``max_pages`` bounds it. Lazy, so a caller
    that stops consuming makes no further requests."""
    seen = set()
    body, _ = client.get_json(path, params, operation=operation)
    for page_number in range(1, max_pages + 1):
        items = items_of(body)
        cursor = cursor_of(body)
        more = bool(cursor) and bool(items) and page_number < max_pages
        if cursor and not items:
            log_event(_log, logging.WARNING, "pipeline.source.empty_page_with_cursor", provider=client.provider,
                      stream=label, page=page_number)
        yield Page(items, has_more=bool(cursor) and bool(items))
        if not more:
            return
        if cursor in seen:
            raise PermanentSourceError(
                SourceFailure.BAD_RESPONSE,
                f"pagination cursor repeated on page {page_number} (fingerprint {_fingerprint(cursor)})",
                provider=client.provider,
            )
        seen.add(cursor)
        body, _ = client.get_json(cursor, None, operation=operation)


@dataclass
class HealthReport:
    source: str
    provider: str
    configured: bool
    missing: List[str] = field(default_factory=list)
    reachable: Optional[bool] = None
    auth: Optional[str] = None  # valid | invalid | not_required | unknown
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    rate_limit: Optional[Dict[str, str]] = None
    error: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return self.configured and bool(self.reachable) and self.auth in {"valid", "not_required"}

    def as_dict(self) -> Dict[str, Any]:
        return {**self.__dict__, "healthy": self.healthy}


def probe_health(
    report: HealthReport,
    client: SourceClient,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    method: str = "GET",
    auth_required: bool = True,
) -> HealthReport:
    import httpx

    try:
        status, headers, latency = client.probe(method, path, params, operation="health")
    except httpx.HTTPError as exc:
        report.reachable, report.error = False, f"{type(exc).__name__}: {exc}"[:300]
        return report
    from app.pipeline.sources.http import rate_limit_headers

    report.reachable, report.status_code = True, status
    report.latency_ms = round(latency * 1000, 1)
    report.rate_limit = rate_limit_headers(headers)
    if status in (401, 403):
        report.auth, report.error = "invalid", f"HTTP {status}"
    elif 200 <= status < 300:
        report.auth = "valid" if auth_required else "not_required"
    else:
        report.auth = "unknown"
        report.error = f"HTTP {status}" + (" (rate limited)" if status == 429 else "")
    return report
