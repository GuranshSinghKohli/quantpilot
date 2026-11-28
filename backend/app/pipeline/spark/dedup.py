"""Deterministic deduplication (window functions; no Python UDFs).

1. **Exact duplicates**: rows with the same ``event_id`` (same source, external
   id and content hash) collapse to one, keeping the lowest ``raw_key``.
2. **Merge with already-processed rows**: a row whose ``event_id`` already
   exists in the target partition keeps the existing row (and its original
   ``processing_job_id``), so reprocessing is a no-op for those rows.
3. **Superseded versions**: per logical record (the contract's ``dedup_key``,
   computed once in Python and never re-derived here) only the newest
   ``ingested_at`` survives, tie-broken by the larger ``event_id``: the same
   order as the canonical guard ``app.repositories._upsert.newer_than_existing``,
   so the processed winner is always the row the loader keeps. dedup_key is
   ``market:{symbol}:{interval}:{trading_date}:{source}``, ``company:cik:{cik}``
   and ``news:{url_hash}``. Titles and names never key a merge.
"""

from __future__ import annotations

from typing import Tuple

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

_RANK = "_dedup_rank"


def drop_exact_duplicates(df: DataFrame) -> DataFrame:
    order = Window.partitionBy("event_id").orderBy(F.col("raw_key").asc())
    return df.withColumn(_RANK, F.row_number().over(order)).where(F.col(_RANK) == 1).drop(_RANK)


def merge_existing(new: DataFrame, existing: DataFrame) -> Tuple[DataFrame, DataFrame]:
    """(union of new rows not yet processed with every existing row, new rows already processed)."""
    already = new.join(existing.select("event_id"), "event_id", "left_semi")
    fresh = new.join(existing.select("event_id"), "event_id", "left_anti")
    return fresh.unionByName(existing), already


def keep_latest_versions(df: DataFrame) -> DataFrame:
    order = Window.partitionBy("dedup_key").orderBy(F.col("ingested_at").desc(), F.col("event_id").desc())
    return df.withColumn(_RANK, F.row_number().over(order)).where(F.col(_RANK) == 1).drop(_RANK)
