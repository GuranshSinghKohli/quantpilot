"""Deterministic object keys for the lake.

    raw/<domain>/year=YYYY/month=MM/day=DD/<event_id>.json        ingestion date (UTC)
    processed/<domain>/year=YYYY/month=MM/day=DD/part-*.parquet   record date
    rejects/<domain>/job_id=<uuid>/part-*.parquet
    _manifests/processing/<job_id>.json

A key is ``/``-separated segments of ``[A-Za-z0-9._=-]``. Segments may not
start with ``.``, which rules out ``..`` traversal and keeps the local
backend's ``.tmp-*`` staging files out of the key space.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Optional, Union

from app.domain.enums import IngestionDomain
from app.pipeline.lake.base import InvalidKey

MAX_KEY_BYTES = 1024
_SEGMENT = re.compile(r"[A-Za-z0-9_=\-][A-Za-z0-9._=\-]*")
_EVENT_ID = re.compile(r"[0-9a-f]{64}")

RAW = "raw"
PROCESSED = "processed"
REJECTS = "rejects"
MANIFESTS = "_manifests"


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise InvalidKey(f"object key must be a non-empty string, got {key!r}")
    if len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise InvalidKey(f"object key longer than {MAX_KEY_BYTES} bytes")
    for segment in key.split("/"):
        if not _SEGMENT.fullmatch(segment):
            raise InvalidKey(f"invalid segment {segment!r} in object key {key!r}")
    return key


def validate_prefix(prefix: str) -> str:
    """A listing prefix: empty, a key, a key ending in ``/``, or a partial last segment."""
    if prefix == "":
        return prefix
    body = prefix[:-1] if prefix.endswith("/") else prefix
    validate_key(body)
    return prefix


def _domain(domain: Union[IngestionDomain, str]) -> str:
    try:
        return IngestionDomain(domain).value
    except ValueError:
        raise InvalidKey(f"unknown lake domain {domain!r}") from None


def partition(day: date) -> str:
    return f"year={day:%Y}/month={day:%m}/day={day:%d}"


def utc_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise InvalidKey("partition timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).date()


def raw_key(domain: Union[IngestionDomain, str], ingested_at: datetime, event_id: str) -> str:
    if not _EVENT_ID.fullmatch(event_id or ""):
        raise InvalidKey(f"event_id must be 64 lowercase hex characters, got {event_id!r}")
    return f"{RAW}/{_domain(domain)}/{partition(utc_date(ingested_at))}/{event_id}.json"


def raw_key_for(event) -> str:
    """Key of the raw object for an ``IngestionEvent``. A redelivered message
    maps to the same key, which is what makes landing idempotent."""
    return raw_key(event.domain, event.ingested_at, event.event_id)


def raw_prefix(domain: Union[IngestionDomain, str], day: Optional[date] = None) -> str:
    base = f"{RAW}/{_domain(domain)}/"
    return base if day is None else f"{base}{partition(day)}/"


def processed_prefix(domain: Union[IngestionDomain, str], day: Optional[date] = None) -> str:
    base = f"{PROCESSED}/{_domain(domain)}/"
    return base if day is None else f"{base}{partition(day)}/"


def rejects_prefix(domain: Union[IngestionDomain, str], job_id: uuid.UUID) -> str:
    return f"{REJECTS}/{_domain(domain)}/job_id={uuid.UUID(str(job_id))}/"


def manifest_key(job_id: uuid.UUID) -> str:
    return f"{MANIFESTS}/processing/{uuid.UUID(str(job_id))}.json"
