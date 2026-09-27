"""Per-domain transformations: normalized columns, ordered quality rules, partition date.

``validate`` applies the envelope and domain rules to raw rows and returns every
row with its normalized columns plus ``reject_code`` / ``reject_message``
(null when valid). Nothing is filtered here; the engine splits valid from rejected.
"""

from __future__ import annotations

from types import ModuleType
from typing import Dict

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from app.pipeline.spark.quality import first_failure
from app.pipeline.spark.schemas import PARTITION_COLUMNS
from app.pipeline.spark.transformers import common, company, market, news
from app.pipeline.spark.transformers.common import Context

DOMAINS: Dict[str, ModuleType] = {"news": news, "market": market, "company": company}


def validate(raw: DataFrame, ctx: Context) -> DataFrame:
    spec = DOMAINS[ctx.domain]
    normalized = raw.withColumns({**common.envelope_columns(ctx), **spec.columns(ctx)})
    code, message = first_failure([*common.envelope_rules(ctx), *spec.rules(ctx)])
    return normalized.withColumns({"reject_code": code, "reject_message": message})


def with_partitions(df: DataFrame, domain: str) -> DataFrame:
    day = DOMAINS[domain].partition_date()
    year, month, dd = PARTITION_COLUMNS
    return df.withColumns({
        year: F.date_format(day, "yyyy"),
        month: F.date_format(day, "MM"),
        dd: F.date_format(day, "dd"),
    })
