"""Deterministic identities for ingested records.

Three identities, computed once here and carried downstream (Kafka key, raw
object key, Spark dedup window, Postgres unique constraints):

- ``dedup_key``     the logical thing in the world (an article, a daily bar, a company)
- ``content_hash``  one version of its content
- ``event_id``      one observation: source + external id + content version
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.domain.identity import sha256_hex, url_hash


def canonical_json(value: Any) -> str:
    """Stable serialization for hashing: sorted keys, no whitespace, UTF-8 kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash_of(fields: dict) -> str:
    return sha256_hex(canonical_json(fields))


def event_id(source: str, external_id: str, content_hash: str) -> str:
    return sha256_hex(f"{source}|{external_id}|{content_hash}")


def news_dedup_key(url: str) -> str:
    # Source-independent: the same article reached via two feeds is one article.
    return f"news:{url_hash(url)}"


def market_external_id(symbol: str, interval: str, trading_date: date) -> str:
    return f"{symbol}:{interval}:{trading_date.isoformat()}"


def market_dedup_key(symbol: str, interval: str, trading_date: date, source: str) -> str:
    # Vendor-specific: two vendors' bars for the same session are different records.
    return f"market:{symbol}:{interval}:{trading_date.isoformat()}:{source}"


def company_dedup_key(cik: str) -> str:
    return f"company:cik:{cik}"
