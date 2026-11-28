"""Minute bars from Massive, mapped onto league BarInput.

The daily adapter in ``app.pipeline.sources.massive`` is not used: it requests
``range/1/day``. This module calls the same ``SourceClient`` with
``range/1/minute``. It does not change that client.

``results[].t`` is the Unix millisecond timestamp for the start of the
aggregate window (Massive custom-bars docs). The stored instant is that
start, in UTC, not the end of the minute.

``adjusted=false``. A page that comes back adjusted is refused.

``next_url`` is followed only when its host equals ``MARKET_BASE_URL``'s host.
Any other host is not requested. A warning names the refused host and omits
the URL, which can carry a query credential.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit

from app.league.repo.types import BarInput
from app.league.sim.calendar import MarketCalendar
from app.league.universe import load_universe

logger = logging.getLogger(__name__)

_TICKER = re.compile(r"^[A-Z][A-Z.]{0,5}$")
_PRICE = Decimal("0.0001")
_VOLUME = Decimal("0.000001")
_PRICE_LIMIT = Decimal(10) ** 10
_VOLUME_LIMIT = Decimal(10) ** 14
_MAX_PAGES = 100
FEED = "massive"


class MarketKeyMissing(RuntimeError):
    """MARKET_API_KEY is empty. The message does not contain a key."""

    def __init__(self) -> None:
        super().__init__("MARKET_API_KEY is not set")


class MassiveResponseError(RuntimeError):
    """The provider body cannot be stored. No price was invented."""


class MinuteClient(Protocol):
    def get_json(
        self,
        target: str,
        params: dict[str, Any] | None = None,
        *,
        operation: str,
    ) -> tuple[Any, Any]:
        """Same shape as SourceClient.get_json: (body, headers)."""


@dataclass(frozen=True)
class FetchResult:
    bars: tuple[BarInput, ...]
    skipped: Mapping[str, int]
    calls: int


@dataclass(frozen=True)
class BackfillSummary:
    tickers: tuple[str, ...]
    inserted: int
    skipped: Mapping[str, int]
    calls: int
    failed: tuple[str, ...]


def resolve_tickers(
    tickers: Sequence[str] | None = None,
    universe_path: str | None = None,
) -> tuple[str, ...]:
    """CLI symbols first, then the universe file. Order is kept. Duplicates drop."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(symbol: str) -> None:
        if not symbol or not _TICKER.fullmatch(symbol) or symbol.endswith("."):
            raise ValueError(f"invalid ticker: {symbol!r}")
        if symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)

    for symbol in tickers or ():
        add(symbol.strip())
    if universe_path:
        for listing in load_universe(universe_path).listings:
            add(listing.ticker)
    if not ordered:
        raise ValueError("pass tickers or a universe file")
    return tuple(ordered)


def build_massive_client(settings: Any):
    """A SourceClient paced at MARKET_REQUESTS_PER_MINUTE. The key is not logged."""
    from app.pipeline.retry import RetryPolicy
    from app.pipeline.sources.http import SourceClient, Throttle

    key = settings.MARKET_API_KEY.get_secret_value()
    if not key:
        raise MarketKeyMissing()
    return SourceClient(
        provider="massive",
        base_url=settings.MARKET_BASE_URL,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout_s=settings.SOURCE_TIMEOUT_SECONDS,
        throttle=Throttle(settings.MARKET_REQUESTS_PER_MINUTE),
        policy=RetryPolicy.from_settings(settings),
        max_retry_after_s=settings.SOURCE_MAX_RETRY_AFTER_SECONDS,
    )


def fetch_minute_bars(
    client: MinuteClient,
    ticker: str,
    start: date,
    end: date,
    calendar: MarketCalendar,
    *,
    base_url: str,
) -> FetchResult:
    """Session bars for one ticker. Raises CalendarUnavailable rather than guessing."""
    if end < start:
        raise ValueError("end is before start")
    path = (
        f"/v2/aggs/ticker/{quote(ticker, safe='.')}/range/1/minute/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params: dict[str, Any] | None = {"adjusted": "false", "sort": "asc", "limit": 50_000}
    expected_host = (urlsplit(base_url).hostname or "").lower()
    counts: Counter[str] = Counter()
    bars: list[BarInput] = []
    seen: set[int] = set()
    calls = 0
    target = path
    for _page in range(_MAX_PAGES):
        body, _headers = client.get_json(target, params, operation="aggs")
        calls += 1
        params = None
        page, nxt = _page_of(body, ticker)
        for raw in page:
            millis = raw.get("t") if isinstance(raw, dict) else None
            if isinstance(millis, int) and not isinstance(millis, bool) and millis in seen:
                counts["duplicate_timestamp"] += 1
                continue
            if isinstance(millis, int) and not isinstance(millis, bool):
                seen.add(millis)
            bar, reason = _bar_from_raw(ticker, raw, calendar)
            if bar is None:
                counts[reason or "malformed"] += 1
                continue
            bars.append(bar)
        if not nxt:
            break
        if not _same_host(nxt, expected_host):
            logger.warning(
                "refusing next_url host %s (expected %s)",
                urlsplit(nxt).hostname or "",
                expected_host,
            )
            counts["next_url_other_host"] += 1
            break
        target = nxt
    else:
        raise MassiveResponseError(f"stopped after {_MAX_PAGES} pages for {ticker}")
    skipped = {reason: count for reason, count in sorted(counts.items()) if count}
    return FetchResult(bars=tuple(bars), skipped=skipped, calls=calls)


def run_backfill(
    session,
    client: MinuteClient,
    tickers: Sequence[str],
    start: date,
    end: date,
    calendar: MarketCalendar,
    *,
    base_url: str,
) -> BackfillSummary:
    """Fetch and upsert. Does not commit. A repeat inserts nothing new."""
    from app.league.repo.bars import upsert_bars

    inserted = 0
    calls = 0
    counts: Counter[str] = Counter()
    for ticker in tickers:
        result = fetch_minute_bars(
            client, ticker, start, end, calendar, base_url=base_url
        )
        calls += result.calls
        counts.update(result.skipped)
        wrote = upsert_bars(session, result.bars)
        inserted += wrote
        already = len(result.bars) - wrote
        if already:
            counts["already_present"] += already
    skipped = {reason: count for reason, count in sorted(counts.items()) if count}
    return BackfillSummary(
        tickers=tuple(tickers),
        inserted=inserted,
        skipped=skipped,
        calls=calls,
        failed=(),
    )


def _same_host(url: str, expected_host: str) -> bool:
    if url.startswith("/") and not url.startswith("//"):
        return True
    return (urlsplit(url).hostname or "").lower() == expected_host and bool(expected_host)


def _page_of(body: Any, ticker: str) -> tuple[list[Any], str | None]:
    if not isinstance(body, dict):
        raise MassiveResponseError("response is not a JSON object")
    if body.get("status") == "ERROR":
        raise MassiveResponseError("provider status is ERROR")
    if body.get("adjusted") is True:
        raise MassiveResponseError("Massive returned adjusted prices; refusing to store them")
    reported = body.get("ticker")
    if reported is not None and reported != ticker:
        raise MassiveResponseError(f"provider ticker {reported!r} is not {ticker!r}")
    results = body.get("results")
    if results is None:
        results = []
    if not isinstance(results, list):
        raise MassiveResponseError("`results` is not a list")
    nxt = body.get("next_url")
    if nxt is None or nxt == "":
        return results, None
    if not isinstance(nxt, str):
        raise MassiveResponseError("next_url is not a string")
    return results, nxt


def _bar_from_raw(
    ticker: str,
    raw: Any,
    calendar: MarketCalendar,
) -> tuple[BarInput | None, str | None]:
    """Return (bar, None) or (None, reason). CalendarUnavailable propagates."""
    if not isinstance(raw, dict):
        return None, "malformed"
    millis = raw.get("t")
    if isinstance(millis, bool) or not isinstance(millis, int) or millis < 0:
        return None, "malformed"
    try:
        stamp = _utc_from_millis(millis)
    except (OverflowError, OSError, ValueError):
        return None, "malformed"
    prices = []
    for field in ("o", "h", "l", "c"):
        number = _decimal(raw.get(field))
        if number is None:
            return None, "malformed"
        price = number.quantize(_PRICE, rounding=ROUND_HALF_EVEN)
        if not price.is_finite() or price <= 0 or price >= _PRICE_LIMIT:
            return None, "non_positive_price" if price.is_finite() and price <= 0 else "malformed"
        prices.append(price)
    volume = _decimal(raw.get("v"))
    if volume is None:
        return None, "malformed"
    volume = volume.quantize(_VOLUME, rounding=ROUND_HALF_EVEN)
    if not volume.is_finite() or volume < 0 or volume >= _VOLUME_LIMIT:
        return None, "malformed"
    if not calendar.is_open(stamp):
        return None, "outside_session"
    opened, high, low, closed = prices
    return (
        BarInput(ticker=ticker, t=stamp, o=opened, h=high, l=low, c=closed, v=volume, feed=FEED),
        None,
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    if isinstance(value, float) and value != value:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _utc_from_millis(millis: int) -> datetime:
    seconds, remainder = divmod(millis, 1000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(milliseconds=remainder)
