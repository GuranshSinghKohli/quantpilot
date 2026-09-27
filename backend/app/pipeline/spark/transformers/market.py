"""Market: daily bars → processed rows partitioned by trading_date.

trading_date is the America/New_York session date the adapter derived; it is
parsed, never recomputed. adj_close and currency stay null when the source
does not supply them.
"""

from __future__ import annotations

from typing import Dict, List

from pyspark.sql import Column
from pyspark.sql import functions as F

from app.pipeline.spark.quality import Rule
from app.pipeline.spark.transformers.common import (
    ISO_DATE,
    SYMBOL,
    Context,
    any_of,
    bad_instant,
    blank,
    instant,
    missing,
    out_of_range,
    r,
    w,
)

DECIMAL = r"^-?\d+(\.\d+)?$"
WHOLE = r"^\d{1,18}$"
PRICES = ("open", "high", "low", "close")
MAX_PRICE = "1e14"  # DECIMAL(20,6) keeps 14 integer digits


def _decimal(column: Column) -> Column:
    return F.when(column.rlike(DECIMAL), column.cast("decimal(20,6)"))


def _bad_decimal(raw: Column) -> Column:
    return raw.isNotNull() & _decimal(raw).isNull()


def columns(ctx: Context) -> Dict[str, Column]:
    return {
        "symbol": F.upper(F.trim(r("symbol"))),
        "interval": F.trim(r("interval")),
        "trading_date": F.when(r("trading_date").rlike(ISO_DATE), F.to_date(r("trading_date"), "yyyy-MM-dd")),
        "bar_timestamp": instant(r("timestamp")),
        **{name: _decimal(r(name)) for name in (*PRICES, "adj_close")},
        "volume": F.when(r("volume").rlike(WHOLE), r("volume").cast("bigint")),
        "currency": r("currency"),
    }


def rules(ctx: Context) -> List[Rule]:
    col = F.col
    prices = [col(p) for p in PRICES]
    expected_id = F.concat_ws(":", col("symbol"), col("interval"), F.date_format(col("trading_date"), "yyyy-MM-dd"))
    return [
        missing({f"record.{name}": blank(r(name)) for name in
                 ("symbol", "interval", "timestamp", "trading_date", *PRICES, "volume")}),
        Rule("invalid_symbol", ~col("symbol").rlike(SYMBOL), F.concat(F.lit("invalid symbol "), r("symbol"))),
        Rule("invalid_interval", col("interval") != F.lit("1d"), F.concat(F.lit("interval "), r("interval"), F.lit(" is not 1d"))),
        Rule("invalid_timestamp", col("trading_date").isNull() | bad_instant(r("timestamp")),
             "trading_date must be YYYY-MM-DD and timestamp ISO 8601 with an offset"),
        Rule("timestamp_out_of_range", out_of_range(col("bar_timestamp"), ctx), "bar timestamp before 1970 or after the job start + 1 day"),
        Rule("invalid_numeric", any_of([_bad_decimal(r(name)) for name in (*PRICES, "adj_close")]),
             F.concat_ws(" ", F.lit("prices must be plain decimals fitting DECIMAL(20,6):"),
                         *[F.concat(F.lit(f"{n}="), F.coalesce(r(n), F.lit("null"))) for n in (*PRICES, "adj_close")])),
        Rule("non_positive_price", any_of([p <= 0 for p in prices] + [col("adj_close") <= 0]
                                          + [p >= F.lit(MAX_PRICE).cast("decimal(20,6)") for p in prices]),
             "prices must be > 0 and below 1e14"),
        Rule("invalid_volume", col("volume").isNull(), F.concat(F.lit("volume "), r("volume"), F.lit(" is not a non-negative whole number"))),
        Rule("high_below_low", col("high") < col("low"), F.concat(F.lit("high "), col("high"), F.lit(" < low "), col("low"))),
        Rule("ohlc_inconsistent", any_of([col("open") < col("low"), col("open") > col("high"),
                                          col("close") < col("low"), col("close") > col("high")]),
             "open and close must lie within [low, high]"),
        Rule("trading_date_mismatch", F.abs(F.datediff(F.to_date(col("bar_timestamp")), col("trading_date"))) > 1,
             "bar timestamp is more than one day from trading_date"),
        Rule("identity_mismatch", w("external_id") != expected_id,
             F.concat(F.lit("external_id "), w("external_id"), F.lit(" != symbol:interval:trading_date "), expected_id)),
        Rule("invalid_currency", col("currency").isNotNull() & ~col("currency").rlike(r"^[A-Z]{3}$"),
             F.concat(F.lit("currency "), col("currency"), F.lit(" is not a 3-letter code"))),
    ]


def partition_date() -> Column:
    return F.col("trading_date")
