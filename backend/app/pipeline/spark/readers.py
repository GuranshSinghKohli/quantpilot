"""Read raw lake objects with the explicit wire schema. Raw objects are only read.

``multiLine`` makes each object one JSON document (the lake writes one event per
object), and PERMISSIVE mode keeps malformed objects as rows with
``_corrupt_record`` set instead of dropping them. Two cases the JSON reader
cannot surface as a single row are detected explicitly: an object with several
documents (a top-level array) and an object with none (empty file).
"""

from __future__ import annotations

from typing import Sequence

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from app.pipeline.lake.keys import RAW_KEY_PATTERN
from app.pipeline.spark.schemas import CORRUPT_RECORD, ENVELOPE_FIELDS, wire_struct

_KEY_EVENT_ID = r"([0-9a-f]{64})\.json$"


def read_raw(spark: SparkSession, domain: str, paths: Sequence[str]) -> DataFrame:
    """One row per JSON document: ``w`` (the document), ``raw_key``, ``key_event_id``,
    ``corrupt_record`` and ``documents`` (how many documents the object held)."""
    parsed = (
        spark.read.schema(wire_struct(domain))
        .option("mode", "PERMISSIVE")
        .option("multiLine", "true")
        .option("columnNameOfCorruptRecord", CORRUPT_RECORD)
        .json(list(paths))
    )
    # file_path is URL-encoded ("year%3D2026"); lake keys never contain "%" or "+".
    raw_key = F.regexp_extract(F.try_url_decode(F.col("_metadata.file_path")), f"({RAW_KEY_PATTERN})$", 1)
    document = F.struct(*[F.col(name) for name in (*ENVELOPE_FIELDS, "record", "original")])
    rows = parsed.select(
        document.alias("w"),
        F.col(CORRUPT_RECORD).alias("corrupt_record"),
        raw_key.alias("raw_key"),
    )
    return rows.withColumns({
        "key_event_id": F.regexp_extract("raw_key", _KEY_EVENT_ID, 1),
        "documents": F.count(F.lit(1)).over(Window.partitionBy("raw_key")),
        "document_rank": F.row_number().over(Window.partitionBy("raw_key").orderBy(F.col("corrupt_record").asc_nulls_first(), F.col("w.event_id"))),
    })


def unread_keys(spark: SparkSession, keys: Sequence[str], rows: DataFrame) -> DataFrame:
    """Planned raw keys that produced no row at all (empty or unreadable objects)."""
    planned = spark.createDataFrame([(key,) for key in keys], "raw_key string")
    return planned.join(rows.select("raw_key").distinct(), "raw_key", "left_anti")
