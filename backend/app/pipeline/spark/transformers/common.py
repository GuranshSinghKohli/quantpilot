"""Envelope normalization and checks shared by every domain (Spark SQL expressions only).

Input rows carry the raw document as struct ``w`` (wire schema), the raw object
key, the corrupt-record text and the number of documents in the object.
Records were already normalized by the ingestion contracts; these expressions
re-apply the same idempotent rules (UTC instants, collapsed whitespace,
upper-case symbols) and verify the contract's identity instead of re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Sequence

from pyspark.sql import Column
from pyspark.sql import functions as F

from app.pipeline.contracts import SOURCES, SUPPORTED_SCHEMA_VERSIONS, SYMBOL_PATTERN
from app.pipeline.spark.quality import Rule

ISO_INSTANT = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})$"
ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
HEX64 = r"^[0-9a-f]{64}$"
UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SYMBOL = f"^{SYMBOL_PATTERN}$"
EPOCH = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class Context:
    domain: str
    job_id: str
    started_at: datetime  # aware
    uri_prefix: str
    encode_key: bool = False  # see plan.uri_prefix

    @property
    def latest_allowed(self) -> str:
        return (self.started_at + timedelta(days=1)).isoformat()


def w(field: str) -> Column:
    return F.col(f"w.{field}")


def r(field: str) -> Column:
    return F.col(f"w.record.{field}")


def blank(column: Column) -> Column:
    return column.isNull() | (F.trim(column) == "")


def text(column: Column) -> Column:
    """Collapse every whitespace run (Unicode-aware) to one space and trim; empty → null."""
    collapsed = F.regexp_replace(F.regexp_replace(column, r"(?U)\s+", " "), r"^ | $", "")
    return F.when(F.length(collapsed) > 0, collapsed)


def instant(column: Column) -> Column:
    """ISO 8601 with an explicit offset → timestamp (UTC); anything else → null."""
    return F.when(column.rlike(ISO_INSTANT), column.cast("timestamp"))


def bad_instant(column: Column) -> Column:
    return column.isNotNull() & instant(column).isNull()


def out_of_range(ts: Column, ctx: Context) -> Column:
    return ts.isNotNull() & (
        (ts < F.lit(EPOCH).cast("timestamp")) | (ts > F.lit(ctx.latest_allowed).cast("timestamp"))
    )


def symbols(column: Column) -> Column:
    """Upper-case, trimmed, deduplicated in first-seen order; null → empty array."""
    cleaned = F.array_distinct(F.transform(column, lambda s: F.upper(F.trim(s))))
    return F.coalesce(cleaned, F.array().cast("array<string>"))


def bad_symbols(column: Column) -> Column:
    return F.exists(column, lambda s: s.isNull() | ~s.rlike(SYMBOL))


def missing(fields: Dict[str, Column]) -> Rule:
    failed = F.lit(False)
    for column in fields.values():
        failed = failed | column
    names = F.concat_ws(", ", *[F.when(column, F.lit(name)) for name, column in fields.items()])
    return Rule("missing_required_field", failed, F.concat(F.lit("missing required field(s): "), names))


def any_of(columns: Sequence[Column]) -> Column:
    result = F.lit(False)
    for column in columns:
        result = result | F.coalesce(column, F.lit(False))
    return result


def envelope_columns(ctx: Context) -> Dict[str, Column]:
    synthetic = [key for key, spec in SOURCES.items() if spec.synthetic]
    return {
        "event_id": w("event_id"),
        "source": w("source"),
        "source_type": w("source_type"),
        "external_id": w("external_id"),
        "dedup_key": w("dedup_key"),
        "content_hash": w("content_hash"),
        "envelope_schema_version": w("schema_version").cast("int"),
        "run_id": w("run_id"),
        "correlation_id": w("correlation_id"),
        "ingested_at": instant(w("ingested_at")),
        "occurred_at": instant(w("occurred_at")),
        "raw_uri": F.concat(F.lit(ctx.uri_prefix),
                            F.regexp_replace("raw_key", "=", "%3D") if ctx.encode_key else F.col("raw_key")),
        "processing_job_id": F.lit(ctx.job_id),
        "synthetic": w("source").isin(synthetic) if synthetic else F.lit(False),
        "original_json": w("original"),
    }


def envelope_rules(ctx: Context) -> List[Rule]:
    sources = {key: spec for key, spec in SOURCES.items() if spec.domain.value == ctx.domain}
    source_type = F.create_map(*[F.lit(x) for key, spec in sources.items() for x in (key, spec.source_type.value)])
    required = ("schema_version", "event_id", "run_id", "source", "source_type", "domain", "external_id",
                "dedup_key", "content_hash", "ingested_at")
    identity = F.sha2(F.concat_ws("|", w("source"), w("external_id"), w("content_hash")), 256)
    return [
        Rule("malformed_json", F.col("corrupt_record").isNotNull(), "not a JSON document with the envelope structure"),
        Rule("multiple_documents", F.col("documents") > 1, F.concat(F.lit("object holds "), F.col("documents"), F.lit(" JSON documents"))),
        missing({**{name: blank(w(name)) for name in required},
                 "original": w("original").isNull(), "record": w("record").isNull()}),
        Rule("unsupported_schema_version", ~w("schema_version").isin([str(v) for v in sorted(SUPPORTED_SCHEMA_VERSIONS)]),
             F.concat(F.lit("schema_version "), w("schema_version"), F.lit(" is not supported"))),
        Rule("domain_mismatch", w("domain") != F.lit(ctx.domain),
             F.concat(F.lit("envelope domain "), w("domain"), F.lit(f" in a {ctx.domain} job"))),
        Rule("invalid_identifier",
             ~w("event_id").rlike(HEX64) | ~w("content_hash").rlike(HEX64) | ~w("run_id").rlike(UUID)
             | (F.length(w("external_id")) > 255) | (F.length(w("dedup_key")) > 300)
             | (F.length(F.coalesce(w("correlation_id"), F.lit(""))) > 128),
             "event_id/content_hash must be 64 lowercase hex, run_id a UUID; external_id <= 255, dedup_key <= 300"),
        Rule("event_id_key_mismatch", w("event_id") != F.col("key_event_id"),
             F.concat(F.lit("event_id "), w("event_id"), F.lit(" does not match the object key"))),
        Rule("unknown_source", ~w("source").isin(list(sources)),
             F.concat(F.lit("source "), w("source"), F.lit(f" is not a registered {ctx.domain} source"))),
        Rule("source_type_mismatch", w("source_type") != source_type[w("source")],
             F.concat(F.lit("source_type "), w("source_type"), F.lit(" does not match source "), w("source"))),
        Rule("record_envelope_mismatch", blank(r("source")) | blank(r("content_hash"))
             | (r("source") != w("source")) | (r("content_hash") != w("content_hash")),
             "record.source and record.content_hash must equal the envelope's"),
        Rule("identity_mismatch", identity != w("event_id"), "event_id != sha256(source|external_id|content_hash)"),
        Rule("invalid_timestamp", bad_instant(w("ingested_at")) | bad_instant(w("occurred_at")) | bad_instant(r("ingested_at")),
             "ingested_at / occurred_at must be ISO 8601 with an explicit offset"),
        Rule("timestamp_out_of_range", out_of_range(instant(w("ingested_at")), ctx) | out_of_range(instant(w("occurred_at")), ctx),
             "ingested_at / occurred_at before 1970 or more than one day after the job started"),
    ]
