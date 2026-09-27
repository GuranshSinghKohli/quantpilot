"""Company: SEC reference rows → processed rows keyed by CIK, one snapshot per ingestion day.

CIK is the identity; names are never used to merge companies. Every ticker the
source lists stays in ``tickers``; missing tickers or exchanges stay null.
"""

from __future__ import annotations

from typing import Dict, List

from pyspark.sql import Column
from pyspark.sql import functions as F

from app.pipeline.spark.quality import Rule
from app.pipeline.spark.transformers.common import (
    SYMBOL,
    Context,
    any_of,
    bad_symbols,
    blank,
    missing,
    r,
    symbols,
    text,
    w,
)

CIK = r"^\d{10}$"


def columns(ctx: Context) -> Dict[str, Column]:
    return {
        "cik": F.trim(r("cik")),
        "name": text(r("name")),
        "ticker": F.upper(F.trim(r("ticker"))),
        "tickers": symbols(r("tickers")),
        "exchange": text(r("exchange")),
    }


def rules(ctx: Context) -> List[Rule]:
    col = F.col
    return [
        missing({"record.external_id": blank(r("external_id")), "record.cik": blank(r("cik")), "record.name": blank(r("name"))}),
        Rule("invalid_cik", ~col("cik").rlike(CIK), F.concat(F.lit("CIK "), r("cik"), F.lit(" is not exactly 10 digits"))),
        Rule("identity_mismatch", (F.trim(r("external_id")) != col("cik")) | (w("external_id") != col("cik")),
             "record.external_id and the envelope external_id must equal the CIK"),
        Rule("invalid_text", any_of([F.length(col("name")) > 255, F.length(col("exchange")) > 32]),
             "name <= 255 and exchange <= 32 characters"),
        Rule("invalid_symbol", bad_symbols(col("tickers")) | (col("ticker").isNotNull() & ~col("ticker").rlike(SYMBOL)),
             F.concat(F.lit("invalid tickers: "), F.coalesce(F.array_join(r("tickers"), ","), F.lit("")))),
        Rule("ticker_mismatch", F.coalesce(col("ticker"), F.lit("")) != F.coalesce(F.get(col("tickers"), 0), F.lit("")),
             "ticker must be the first entry of tickers"),
    ]


def partition_date() -> Column:
    return F.to_date(F.col("ingested_at"))
