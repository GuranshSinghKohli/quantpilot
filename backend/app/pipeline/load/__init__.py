"""Canonical loading (Step 2.7): processed Parquet → validation → newest-wins upserts → PostgreSQL.

Manifest-driven: a COMPLETED processing job's manifest names the files; nothing
else in the lake is read. No raw data, providers, Kafka, Spark or HTTP here.
"""

from app.pipeline.load.domains import LOADERS, CompanyLoader, MarketLoader, NewsLoader
from app.pipeline.load.errors import (
    LoadConfigError,
    LoadError,
    LoadValidationError,
    ManifestMismatch,
    ReconciliationError,
)
from app.pipeline.load.runner import DEFAULT_BATCH_SIZE, CanonicalLoadRunner, LoadCounts, LoadResult
from app.pipeline.load.source import select_jobs

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "LOADERS",
    "CanonicalLoadRunner",
    "CompanyLoader",
    "LoadConfigError",
    "LoadCounts",
    "LoadError",
    "LoadResult",
    "LoadValidationError",
    "ManifestMismatch",
    "MarketLoader",
    "NewsLoader",
    "ReconciliationError",
    "select_jobs",
]
