"""Massive (formerly Polygon.io) REST adapters: ticker news and daily aggregates.

Docs: https://massive.com/docs/rest/stocks/news and
https://massive.com/docs/rest/stocks/aggregates/custom-bars. Both endpoints are
in the free "Stocks Basic" plan (5 requests/minute, 2 years of history) and
paginate with ``next_url``. The key is sent as ``Authorization: Bearer``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domain.identity import normalize_url, sha256_hex
from app.pipeline.contracts import SOURCES, MarketRecord, NewsRecord, is_symbol
from app.pipeline.sources.base import (
    HealthReport,
    IngestRequest,
    NormalizationError,
    Page,
    SourceAdapter,
    SourceItem,
    Stream,
    follow_cursor,
    probe_health,
    require,
    validation_details,
)
from app.pipeline.sources.http import PermanentSourceError, SourceClient, SourceFailure

PROVIDER = "massive"
NEWS_PATH = "/v2/reference/news"
_EXCHANGE_TZ = ZoneInfo("America/New_York")  # Massive day bars start at 00:00 ET


def _results(client: SourceClient, body: Any) -> List[Dict[str, Any]]:
    if not isinstance(body, dict):
        raise PermanentSourceError(SourceFailure.BAD_RESPONSE, "response is not a JSON object", provider=client.provider)
    if body.get("status") == "ERROR":
        raise PermanentSourceError(SourceFailure.BAD_RESPONSE, f"provider error: {str(body.get('error'))[:300]}",
                                   provider=client.provider)
    results = body.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise PermanentSourceError(SourceFailure.BAD_RESPONSE, "`results` is not a list", provider=client.provider)
    return results


def _next_url(body: Any) -> Optional[str]:
    value = body.get("next_url") if isinstance(body, dict) else None
    return value if isinstance(value, str) and value else None


class MassiveNewsAdapter(SourceAdapter):
    spec = SOURCES["massive.news"]
    provider = PROVIDER

    def __init__(self, client: SourceClient) -> None:
        self.client = client

    def streams(self, request: IngestRequest) -> List[Stream]:
        # One stream per ticker; no tickers → one market-wide stream.
        tickers = list(request.symbols) or [None]
        return [Stream(label=t or "*", pages=lambda t=t: self._pages(request, t)) for t in tickers]

    def _pages(self, request: IngestRequest, ticker: Optional[str]) -> Iterator[Page]:
        params: Dict[str, Any] = {
            "published_utc.gte": _rfc3339(request.start),
            "published_utc.lte": _rfc3339(request.end),
            "order": "asc",
            "sort": "published_utc",
            "limit": min(request.page_size, 1000),
        }
        if ticker:
            params["ticker"] = ticker

        def items_of(body):
            return [SourceItem(original=a, ref=str(a.get("id") or a.get("article_url") or "?")[:120])
                    if isinstance(a, dict) else SourceItem(original={"_non_object": a}, ref="non-object")
                    for a in _results(self.client, body)]

        return follow_cursor(self.client, NEWS_PATH, params, operation="news", max_pages=request.max_pages,
                             label=ticker or "*", items_of=items_of, cursor_of=_next_url)

    def normalize(self, item: SourceItem, *, ingested_at: datetime) -> NewsRecord:
        a = item.original
        require(a, "title", "article_url", "published_utc")
        publisher = a.get("publisher")
        if publisher is not None and not isinstance(publisher, dict):
            raise NormalizationError("publisher is not an object", [{"field": "publisher", "error": "not an object"}])
        publisher_name = (publisher or {}).get("name")
        published_at = _parse_instant(a["published_utc"], "published_utc")
        tickers = a.get("tickers") or []
        if not isinstance(tickers, list):
            raise NormalizationError("tickers is not a list", [{"field": "tickers", "error": "not a list"}])
        try:
            return NewsRecord.create(
                source=self.source,
                external_id=a.get("id") or derived_news_id(str(a["article_url"]), publisher_name, published_at),
                title=a["title"],
                url=a["article_url"],
                published_at=published_at,
                author=a.get("author") or None,
                publisher=publisher_name or None,
                summary=a.get("description") or None,
                # Non-equity tags (e.g. "X:BTCUSD") stay in the raw item only.
                tickers=[t for t in tickers if is_symbol(t)],
                ingested_at=ingested_at,
            )
        except ValidationError as exc:
            raise NormalizationError(f"article {item.ref} failed the news contract", validation_details(exc)) from exc

    def health(self) -> HealthReport:
        report = HealthReport(self.source, self.provider, configured=True)
        return probe_health(report, self.client, NEWS_PATH, {"limit": 1})


class MassiveMarketAdapter(SourceAdapter):
    spec = SOURCES["massive.market_daily"]
    provider = PROVIDER

    def __init__(self, client: SourceClient, *, health_symbol: str = "AAPL") -> None:
        self.client = client
        self.health_symbol = health_symbol

    def streams(self, request: IngestRequest) -> List[Stream]:
        if not request.symbols:
            raise ValueError("market ingestion needs at least one symbol")
        return [Stream(label=s, pages=lambda s=s: self._pages(request, s)) for s in request.symbols]

    def _path(self, symbol: str, start: date, end: date) -> str:
        return f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}"

    def _pages(self, request: IngestRequest, symbol: str) -> Iterator[Page]:
        # Raw (split-unadjusted) prices; ``limit`` counts base aggregates per page.
        params = {"adjusted": "false", "sort": "asc", "limit": min(request.page_size, 50_000)}

        def items_of(body):
            bars = _results(self.client, body)
            ticker = body.get("ticker") or symbol
            context = {"ticker": ticker, "adjusted": body.get("adjusted"), "request_id": body.get("request_id")}
            return [SourceItem(original={**context, "bar": bar}, ref=f"{ticker}:{bar.get('t') if isinstance(bar, dict) else '?'}")
                    for bar in bars]

        path = self._path(symbol, request.start.date(), request.end.date())
        return follow_cursor(self.client, path, params, operation="aggs", max_pages=request.max_pages,
                             label=symbol, items_of=items_of, cursor_of=_next_url)

    def normalize(self, item: SourceItem, *, ingested_at: datetime) -> MarketRecord:
        bar = item.original.get("bar")
        if not isinstance(bar, dict):
            raise NormalizationError("bar is not an object", [{"field": "bar", "error": "not an object"}])
        require(bar, "o", "h", "l", "c", "v", "t")
        t = bar["t"]
        if isinstance(t, bool) or not isinstance(t, int):
            raise NormalizationError("t is not an integer millisecond timestamp", [{"field": "t", "error": "not an integer"}])
        timestamp = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        try:
            return MarketRecord.create(
                source=self.source,
                symbol=item.original.get("ticker"),
                timestamp=timestamp,
                trading_date=timestamp.astimezone(_EXCHANGE_TZ).date(),
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
                # Not in the response (adjusted=false, no currency field): unknown, not guessed.
                adj_close=None,
                currency=None,
                ingested_at=ingested_at,
            )
        except ValidationError as exc:
            raise NormalizationError(f"bar {item.ref} failed the market contract", validation_details(exc)) from exc

    def health(self) -> HealthReport:
        report = HealthReport(self.source, self.provider, configured=True)
        today = datetime.now(timezone.utc).date()
        return probe_health(report, self.client, self._path(self.health_symbol, today - timedelta(days=7), today),
                            {"adjusted": "false", "limit": 10})


def derived_news_id(url: str, publisher: Optional[str], published_at: datetime) -> str:
    """Identity for an article without a provider id: canonical URL + publisher + time."""
    return "derived:" + sha256_hex(f"{normalize_url(url)}|{publisher or ''}|{published_at.isoformat()}")


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise NormalizationError(f"{field} is not a string", [{"field": field, "error": "not a string"}])
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalizationError(f"{field} is not RFC 3339", [{"field": field, "error": "not RFC 3339"}]) from exc
    if parsed.tzinfo is None:
        raise NormalizationError(f"{field} has no UTC offset", [{"field": field, "error": "naive timestamp"}])
    return parsed.astimezone(timezone.utc)
