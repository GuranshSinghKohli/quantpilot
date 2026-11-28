"""News: Massive article metadata → processed rows partitioned by published date (UTC)."""

from __future__ import annotations

from typing import Dict, List

from pyspark.sql import Column
from pyspark.sql import functions as F

from app.pipeline.spark.quality import Rule
from app.pipeline.spark.transformers.common import (
    Context,
    any_of,
    bad_instant,
    bad_symbols,
    blank,
    instant,
    missing,
    out_of_range,
    r,
    symbols,
    text,
    w,
)

URL = r"(?i)^https?://[^\s/?#]+[^\s]*$"


def columns(ctx: Context) -> Dict[str, Column]:
    return {
        "title": text(r("title")),
        "url": F.trim(r("url")),
        "published_at": instant(r("published_at")),
        "author": text(r("author")),
        "publisher": text(r("publisher")),
        "summary": text(r("summary")),
        "tickers": symbols(r("tickers")),
    }


def rules(ctx: Context) -> List[Rule]:
    col = F.col
    return [
        missing({"record.external_id": blank(r("external_id")), "record.title": blank(r("title")),
                 "record.url": blank(r("url")), "record.published_at": blank(r("published_at"))}),
        Rule("identity_mismatch", r("external_id") != w("external_id"), "record.external_id differs from the envelope external_id"),
        Rule("invalid_text", any_of([F.length(r("external_id")) > 255, F.length(col("title")) > 512,
                                     F.length(col("author")) > 255, F.length(col("publisher")) > 255,
                                     F.length(col("summary")) > 20_000]),
             "external_id <= 255, title <= 512, author/publisher <= 255, summary <= 20000 characters"),
        Rule("invalid_url", ~col("url").rlike(URL) | (F.length(col("url")) > 2048), "url must be an absolute http(s) URL (<= 2048)"),
        Rule("invalid_timestamp", bad_instant(r("published_at")), F.concat(F.lit("published_at "), r("published_at"), F.lit(" is not ISO 8601 with an offset"))),
        Rule("timestamp_out_of_range", out_of_range(col("published_at"), ctx), "published_at before 1970 or after the job start + 1 day"),
        Rule("invalid_symbol", bad_symbols(col("tickers")), F.concat(F.lit("invalid tickers: "), F.array_join(r("tickers"), ","))),
    ]


def partition_date() -> Column:
    return F.to_date(F.col("published_at"))
