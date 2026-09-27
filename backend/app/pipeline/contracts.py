"""Canonical data contracts for ingestion.

Every record crossing a pipeline boundary (Kafka message, raw lake object, DLQ
entry) is one of these models. ``IngestionEvent.original`` is the only untyped
field: it keeps the verbatim source payload so processing is reproducible.

Records normalize on construction (UTC timestamps, upper-case symbols, 6 dp
decimals, collapsed whitespace), so the content hash is stable across fetches.
Build records with ``Record.create(...)`` and envelopes with ``build_event``.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Optional, Tuple, Union

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.domain.enums import IngestionDomain as Domain
from app.domain.identity import normalize_url
from app.pipeline import identity

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


class DataSourceType(StrEnum):
    """What kind of data a source provides. ``news`` and ``market_data`` match
    ``app.domain.enums.SourceType`` so news rows map onto ``sources`` directly."""

    NEWS = "news"
    MARKET_DATA = "market_data"
    REFERENCE_DATA = "reference_data"


@dataclass(frozen=True)
class SourceSpec:
    key: str
    domain: Domain
    source_type: DataSourceType
    provider: str
    description: str
    # Deterministic test data for exercising the pipeline. Never real data;
    # later steps must keep it out of canonical tables.
    synthetic: bool = False


SOURCES: Dict[str, SourceSpec] = {
    spec.key: spec
    for spec in (
        SourceSpec(
            "massive.market_daily",
            Domain.MARKET,
            DataSourceType.MARKET_DATA,
            "massive",
            "Massive (formerly Polygon.io) daily aggregates, unadjusted (adjusted=false); no currency or adj_close",
        ),
        SourceSpec(
            "massive.news",
            Domain.NEWS,
            DataSourceType.NEWS,
            "massive",
            "Massive /v2/reference/news: article metadata, description, tickers; no article bodies",
        ),
        SourceSpec(
            "sec.company_tickers",
            Domain.COMPANY,
            DataSourceType.REFERENCE_DATA,
            "sec",
            "SEC company_tickers_exchange.json: CIK, name, tickers, exchange (keyless reference data)",
        ),
        SourceSpec(
            "synthetic.market",
            Domain.MARKET,
            DataSourceType.MARKET_DATA,
            "synthetic",
            "SYNTHETIC deterministic daily bars for pipeline tests and demos; not market data",
            synthetic=True,
        ),
    )
}


# --- field types -----------------------------------------------------------------

_SYMBOL = re.compile(r"\^?[A-Z0-9][A-Z0-9.\-=]{0,15}")
_SIX_DP = Decimal("0.000001")
_PRICE_LIMIT = Decimal("1e14")  # DECIMAL(20,6) holds 14 integer digits


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _collapse(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def _symbol(value: str) -> str:
    upper = value.strip().upper()
    if not _SYMBOL.fullmatch(upper):
        raise ValueError(f"invalid symbol {value!r}")
    return upper


def is_symbol(value: Any) -> bool:
    return isinstance(value, str) and bool(_SYMBOL.fullmatch(value.strip().upper()))


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("price must be finite")
        value = repr(value)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc
    if not number.is_finite():
        raise ValueError("price must be finite")
    return number.quantize(_SIX_DP, rounding=ROUND_HALF_EVEN)


def _price(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("price must be > 0")
    if value >= _PRICE_LIMIT:
        raise ValueError("price out of range")
    return value


def _volume(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("boolean is not a volume")
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise ValueError("volume must be a whole number")
        return int(value)
    return value


def _tickers(values: Any) -> Tuple[str, ...]:
    seen: List[str] = []
    for raw in values or ():
        symbol = _symbol(raw)
        if symbol not in seen:
            seen.append(symbol)
    return tuple(seen)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Symbol = Annotated[str, AfterValidator(_symbol)]
Price = Annotated[Decimal, BeforeValidator(_decimal), AfterValidator(_price)]
Volume = Annotated[int, BeforeValidator(_volume), Field(ge=0)]
Tickers = Annotated[Tuple[str, ...], BeforeValidator(_tickers)]
Text = Annotated[Optional[str], AfterValidator(_collapse)]

_PLACEHOLDER_HASH = "0" * 64
_HASH_EXCLUDED = {"content_hash", "ingested_at"}


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _Record(_Contract):
    DOMAIN: ClassVar[Domain]

    source: str
    content_hash: Sha256
    ingested_at: UtcDatetime

    @field_validator("source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        spec = SOURCES.get(value)
        if spec is None:
            raise ValueError(f"unknown source {value!r}")
        if spec.domain != cls.DOMAIN:
            raise ValueError(f"source {value!r} does not produce {cls.DOMAIN} records")
        return value

    @classmethod
    def create(cls, **fields: Any):
        """Validate, normalize, then stamp the content hash of the normalized fields."""
        draft = cls.model_validate({**fields, "content_hash": _PLACEHOLDER_HASH})
        return draft.model_copy(update={"content_hash": draft.compute_content_hash()})

    def compute_content_hash(self) -> str:
        return identity.content_hash_of(self.model_dump(mode="json", exclude=_HASH_EXCLUDED))

    @property
    def logical_external_id(self) -> str:
        raise NotImplementedError

    @property
    def dedup_key(self) -> str:
        raise NotImplementedError

    @property
    def occurred_at(self) -> Optional[datetime]:
        return None


class NewsRecord(_Record):
    DOMAIN: ClassVar[Domain] = Domain.NEWS

    external_id: str = Field(min_length=1, max_length=255)
    title: Annotated[str, AfterValidator(_collapse), Field(min_length=1, max_length=512)]
    url: str = Field(max_length=2048)
    published_at: UtcDatetime
    author: Text = Field(default=None, max_length=255)
    publisher: Text = Field(default=None, max_length=255)
    summary: Text = Field(default=None, max_length=20_000)
    tickers: Tickers = ()

    @field_validator("url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        normalized = normalize_url(value)
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("url must be http(s)")
        return value

    @property
    def logical_external_id(self) -> str:
        return self.external_id

    @property
    def dedup_key(self) -> str:
        return identity.news_dedup_key(self.url)

    @property
    def occurred_at(self) -> Optional[datetime]:
        return self.published_at


class MarketRecord(_Record):
    DOMAIN: ClassVar[Domain] = Domain.MARKET

    symbol: Symbol
    interval: Literal["1d"] = "1d"
    timestamp: UtcDatetime
    trading_date: date
    open: Price
    high: Price
    low: Price
    close: Price
    adj_close: Optional[Price] = None
    volume: Volume
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _consistent_bar(self) -> "MarketRecord":
        if not (self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high):
            raise ValueError("OHLC inconsistent: need low <= open,close <= high")
        # The bar timestamp is the session open in UTC; it can sit one calendar day
        # off the exchange-local session date, never more.
        if abs((self.timestamp.date() - self.trading_date).days) > 1:
            raise ValueError("timestamp does not belong to trading_date")
        return self

    @property
    def logical_external_id(self) -> str:
        return identity.market_external_id(self.symbol, self.interval, self.trading_date)

    @property
    def dedup_key(self) -> str:
        return identity.market_dedup_key(self.symbol, self.interval, self.trading_date, self.source)

    @property
    def occurred_at(self) -> Optional[datetime]:
        return self.timestamp


class CompanyRecord(_Record):
    DOMAIN: ClassVar[Domain] = Domain.COMPANY

    external_id: str
    cik: str
    name: Annotated[str, AfterValidator(_collapse), Field(min_length=1, max_length=255)]
    ticker: Optional[Symbol] = None
    tickers: Tickers = ()
    exchange: Optional[str] = Field(default=None, max_length=32)

    @field_validator("cik", "external_id", mode="before")
    @classmethod
    def _pad_cik(cls, value: Any) -> str:
        text = str(value).strip()
        if not text.isdigit() or len(text) > 10:
            raise ValueError(f"CIK must be up to 10 digits: {value!r}")
        return text.zfill(10)

    @model_validator(mode="after")
    def _consistent_identity(self) -> "CompanyRecord":
        if self.external_id != self.cik:
            raise ValueError("external_id must equal cik")
        expected = self.tickers[0] if self.tickers else None
        if self.ticker != expected:
            raise ValueError("ticker must be the first entry of tickers")
        return self

    @property
    def logical_external_id(self) -> str:
        return self.cik

    @property
    def dedup_key(self) -> str:
        return identity.company_dedup_key(self.cik)


Record = Union[NewsRecord, MarketRecord, CompanyRecord]


# --- envelope --------------------------------------------------------------------


class _EventBase(_Contract):
    schema_version: int = Field(ge=1)
    event_id: Sha256
    run_id: uuid.UUID
    source: str
    source_type: DataSourceType
    external_id: str = Field(min_length=1, max_length=255)
    dedup_key: str = Field(min_length=1, max_length=300)
    content_hash: Sha256
    occurred_at: Optional[UtcDatetime] = None
    ingested_at: UtcDatetime
    original: Dict[str, JsonValue]
    # Trace id shared by everything one trigger produced (defaults to run_id).
    correlation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class NewsEvent(_EventBase):
    domain: Literal["news"]
    record: NewsRecord


class MarketEvent(_EventBase):
    domain: Literal["market"]
    record: MarketRecord


class CompanyEvent(_EventBase):
    domain: Literal["company"]
    record: CompanyRecord


IngestionEvent = Annotated[Union[NewsEvent, MarketEvent, CompanyEvent], Field(discriminator="domain")]
EVENT_ADAPTER: TypeAdapter = TypeAdapter(IngestionEvent)

_EVENT_TYPES = {Domain.NEWS: NewsEvent, Domain.MARKET: MarketEvent, Domain.COMPANY: CompanyEvent}


def build_event(
    record: Record,
    *,
    run_id: uuid.UUID,
    original: Dict[str, Any],
    ingested_at: Optional[datetime] = None,
    correlation_id: Optional[str] = None,
) -> Union[NewsEvent, MarketEvent, CompanyEvent]:
    """Wrap a record in its envelope with every derived identity filled in."""
    spec = SOURCES[record.source]
    external_id = record.logical_external_id
    return _EVENT_TYPES[spec.domain](
        schema_version=SCHEMA_VERSION,
        event_id=identity.event_id(record.source, external_id, record.content_hash),
        run_id=run_id,
        source=record.source,
        source_type=spec.source_type,
        domain=spec.domain.value,
        external_id=external_id,
        dedup_key=record.dedup_key,
        content_hash=record.content_hash,
        occurred_at=record.occurred_at,
        ingested_at=ingested_at or record.ingested_at,
        record=record,
        original=original,
        correlation_id=correlation_id or str(run_id),
    )


# --- dead letters ----------------------------------------------------------------


class DeadLetter(_Contract):
    """Value on the DLQ topic: the original bytes plus enough context to debug."""

    schema_version: int = SCHEMA_VERSION
    dlq_id: uuid.UUID
    component: Literal["ingestor", "lake_writer", "loader"]
    failure_reason: str
    error_type: str
    error_message: str = Field(max_length=4000)
    error_details: List[Dict[str, JsonValue]] = Field(default_factory=list)
    source: Optional[str] = None
    event_id: Optional[str] = None
    run_id: Optional[uuid.UUID] = None
    original_topic: Optional[str] = None
    partition: Optional[int] = None
    offset: Optional[int] = None
    original_key: Optional[str] = None
    original_value: str
    value_encoding: Literal["utf8", "base64"]
    original_truncated: bool = False
    retry_count: int = Field(ge=0)
    first_failed_at: UtcDatetime
    failed_at: UtcDatetime
