"""Explicit Spark schemas. No schema inference anywhere in processing.

Two schemas per domain:

- **wire**: the raw object exactly as the lake writer stored it (the JSON of an
  ``IngestionEvent``). Every scalar is read as a string, so a value of the wrong
  type reaches the quality checks and gets a specific reject code instead of a
  silent null. ``original`` is read as its JSON text.
- **processed**: typed output columns (lineage + domain fields). Partition
  columns (year/month/day) live in the path, not in the files.

The column specs are plain data so the manifest, docs and unit tests can use
them without a JVM; ``wire_struct`` / ``processed_struct`` build the pyspark types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

PROCESSED_SCHEMA_VERSION = 1
CORRUPT_RECORD = "_corrupt_record"
PARTITION_COLUMNS: Tuple[str, ...] = ("year", "month", "day")

# IngestionEvent top-level fields other than `record` and `original`.
ENVELOPE_FIELDS: Tuple[str, ...] = (
    "schema_version", "event_id", "run_id", "source", "source_type", "domain", "external_id",
    "dedup_key", "content_hash", "occurred_at", "ingested_at", "correlation_id",
)

# Record fields per domain, as they appear in the JSON. Must equal the contract
# models' fields (tests/unit/pipeline/test_processing_pure.py checks this).
_ARRAY = "array<string>"
WIRE_RECORD_FIELDS: Dict[str, Dict[str, str]] = {
    "news": {
        "source": "string", "content_hash": "string", "ingested_at": "string", "external_id": "string",
        "title": "string", "url": "string", "published_at": "string", "author": "string",
        "publisher": "string", "summary": "string", "tickers": _ARRAY,
    },
    "market": {
        "source": "string", "content_hash": "string", "ingested_at": "string", "symbol": "string",
        "interval": "string", "timestamp": "string", "trading_date": "string", "open": "string",
        "high": "string", "low": "string", "close": "string", "adj_close": "string", "volume": "string",
        "currency": "string",
    },
    "company": {
        "source": "string", "content_hash": "string", "ingested_at": "string", "external_id": "string",
        "cik": "string", "name": "string", "ticker": "string", "tickers": _ARRAY, "exchange": "string",
    },
}


@dataclass(frozen=True)
class Col:
    name: str
    type: str  # string | int | bigint | boolean | date | timestamp | decimal(20,6) | array<string>
    required: bool
    description: str

    def as_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "required": self.required, "description": self.description}


LINEAGE_COLUMNS: Tuple[Col, ...] = (
    Col("event_id", "string", True, "sha256(source|external_id|content_hash); primary identity"),
    Col("source", "string", True, "source adapter key, e.g. massive.news"),
    Col("source_type", "string", True, "news | market_data | reference_data"),
    Col("external_id", "string", True, "the source's identifier for the logical record"),
    Col("dedup_key", "string", True, "logical-record key from the ingestion contract"),
    Col("content_hash", "string", True, "sha256 of the normalized record fields"),
    Col("envelope_schema_version", "int", True, "IngestionEvent schema_version"),
    Col("run_id", "string", True, "ingestion_runs.id that fetched the record"),
    Col("correlation_id", "string", False, "trace id of the ingestion trigger"),
    Col("ingested_at", "timestamp", True, "when the adapter fetched the record (UTC)"),
    Col("occurred_at", "timestamp", False, "published_at / bar timestamp; null for companies"),
    Col("raw_key", "string", True, "raw lake object key"),
    Col("raw_uri", "string", True, "raw lake object URI (same form as ingestion_records.raw_uri)"),
    Col("processing_job_id", "string", True, "processing_jobs.id that first produced this row"),
    Col("synthetic", "boolean", True, "true for SYNTHETIC test sources; never real data"),
    Col("original_json", "string", True, "verbatim provider payload (JSON text) from the raw object"),
)

DOMAIN_COLUMNS: Dict[str, Tuple[Col, ...]] = {
    "news": (
        Col("title", "string", True, "headline, whitespace collapsed"),
        Col("url", "string", True, "article URL as supplied (trimmed)"),
        Col("published_at", "timestamp", True, "publication instant (UTC)"),
        Col("author", "string", False, "as supplied; null when absent"),
        Col("publisher", "string", False, "publisher name; null when absent"),
        Col("summary", "string", False, "provider description; no article bodies"),
        Col("tickers", _ARRAY, True, "symbols the source associated, upper-case, deduplicated in order"),
    ),
    "market": (
        Col("symbol", "string", True, "upper-case ticker"),
        Col("interval", "string", True, "bar interval (1d)"),
        Col("trading_date", "date", True, "exchange session date (America/New_York), as defined by the adapter"),
        Col("bar_timestamp", "timestamp", True, "bar start instant (UTC)"),
        Col("open", "decimal(20,6)", True, "unadjusted"),
        Col("high", "decimal(20,6)", True, "unadjusted"),
        Col("low", "decimal(20,6)", True, "unadjusted"),
        Col("close", "decimal(20,6)", True, "unadjusted"),
        Col("adj_close", "decimal(20,6)", False, "null: not supplied by the current provider"),
        Col("volume", "bigint", True, "shares traded"),
        Col("currency", "string", False, "null: not supplied by the current provider"),
    ),
    "company": (
        Col("cik", "string", True, "SEC CIK, 10 digits; the stable company identifier"),
        Col("name", "string", True, "registrant name, whitespace collapsed"),
        Col("ticker", "string", False, "first listed ticker; null when none"),
        Col("tickers", _ARRAY, True, "every ticker the source lists for the CIK, in source order"),
        Col("exchange", "string", False, "as supplied; null when absent"),
    ),
}

# Which date each processed partition represents.
PARTITION_BASIS: Dict[str, str] = {
    "news": "published_at, UTC calendar date",
    "market": "trading_date (America/New_York session date)",
    "company": "ingested_at, UTC calendar date (one reference snapshot per day)",
}

# Deterministic row order inside each written partition.
SORT_KEYS: Dict[str, Tuple[str, ...]] = {
    "news": ("published_at", "event_id"),
    "market": ("symbol", "trading_date", "source", "event_id"),
    "company": ("cik", "event_id"),
}


def processed_columns(domain: str) -> List[Col]:
    return [*LINEAGE_COLUMNS, *DOMAIN_COLUMNS[domain]]


def processed_column_names(domain: str) -> List[str]:
    return [c.name for c in processed_columns(domain)]


def _spark_type(name: str):
    from pyspark.sql import types as T

    simple = {
        "string": T.StringType(), "int": T.IntegerType(), "bigint": T.LongType(), "boolean": T.BooleanType(),
        "date": T.DateType(), "timestamp": T.TimestampType(), "decimal(20,6)": T.DecimalType(20, 6),
        _ARRAY: T.ArrayType(T.StringType(), containsNull=True),
    }
    return simple[name]


def wire_struct(domain: str):
    """Schema for reading raw objects of one domain (plus the corrupt-record column)."""
    from pyspark.sql import types as T

    record = T.StructType([T.StructField(n, _spark_type(t), True) for n, t in WIRE_RECORD_FIELDS[domain].items()])
    return T.StructType(
        [T.StructField(n, T.StringType(), True) for n in ENVELOPE_FIELDS]
        + [
            T.StructField("record", record, True),
            T.StructField("original", T.StringType(), True),
            T.StructField(CORRUPT_RECORD, T.StringType(), True),
        ]
    )


def processed_struct(domain: str):
    """Schema of the Parquet data files (partition columns excluded)."""
    from pyspark.sql import types as T

    return T.StructType([T.StructField(c.name, _spark_type(c.type), True) for c in processed_columns(domain)])
