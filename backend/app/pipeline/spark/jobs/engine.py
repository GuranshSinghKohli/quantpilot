"""Spark engine for one processing job: read → validate → reject/keep → dedup → merge → write.

Everything row-level is Spark SQL (no Python UDFs, no row loops). The driver
collects only small things: partition names, per-code reject counts, and it
streams reject rows and new event ids with ``toLocalIterator`` for the runner.

Merge-on-read: the target partitions' existing rows are read back (explicit
schema), merged, deduplicated and the result is ``localCheckpoint``-ed before
the overwrite, so the write never reads from the files it is replacing. A local
checkpoint lives on executors; losing one fails the job (FAILED, rerun) rather
than producing partial output silently.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.pipeline.lake import ObjectStore
from app.pipeline.spark.config import ProcessingConfigError, SparkConfig, spark_version
from app.pipeline.spark.jobs.runner import EngineResult, JobInfo, ProcessingCounts
from app.pipeline.spark.plan import InputPlan, spark_path, uri_prefix
from app.pipeline.spark.schemas import PARTITION_COLUMNS, processed_column_names, processed_struct
from app.pipeline.spark.writers import RejectRow, write_processed


class SparkEngine:
    name = "spark"

    def __init__(self, store: ObjectStore, config: SparkConfig, session=None) -> None:
        if session is None and spark_version() is None:
            raise ProcessingConfigError("pyspark is not installed; run processing in the spark image (make spark-*)")
        self.store = store
        self.config = config
        self._spark = session
        self._owns_session = session is None
        self.version = spark_version()

    @property
    def master(self) -> Optional[str]:
        return self.config.master

    @property
    def spark(self):
        if self._spark is None:
            from app.pipeline.spark.session import build_session

            self._spark = build_session(self.config)
        return self._spark

    def close(self) -> None:
        if self._spark is not None and self._owns_session:
            self._spark.stop()
            self._spark = None

    def process(self, plan: InputPlan, job: JobInfo) -> EngineResult:
        from pyspark import StorageLevel
        from pyspark.sql import functions as F

        from app.pipeline.spark import dedup, readers
        from app.pipeline.spark.transformers import validate, with_partitions
        from app.pipeline.spark.transformers.common import Context

        spark = self.spark
        domain = plan.domain
        prefix, encode = uri_prefix(self.store)
        ctx = Context(domain=domain, job_id=str(job.job_id), started_at=job.started_at, uri_prefix=prefix,
                      encode_key=encode)
        columns = processed_column_names(domain)
        cached = []

        def keep(df):
            cached.append(df.persist(StorageLevel.MEMORY_AND_DISK))
            return cached[-1]

        rows = readers.read_raw(spark, domain, [spark_path(self.store, key) for key in plan.keys])
        checked = keep(validate(rows.where(F.col("document_rank") == 1), ctx))
        unread = readers.unread_keys(spark, plan.keys, checked)

        rejected = checked.where(F.col("reject_code").isNotNull()).select(
            "raw_key", F.col("reject_code").alias("code"), F.col("reject_message").alias("message"),
            F.col("w.source").alias("source"), F.col("w.event_id").alias("envelope_event_id"),
        ).unionByName(unread.select(
            "raw_key", F.lit("unreadable_object").alias("code"),
            F.lit("the object produced no JSON document (empty or unreadable)").alias("message"),
            F.lit(None).cast("string").alias("source"), F.lit(None).cast("string").alias("envelope_event_id"),
        ))
        rejected = keep(rejected)
        valid = checked.where(F.col("reject_code").isNull())

        counts = ProcessingCounts(input_objects=len(plan.keys))
        counts.valid_rows = valid.count()
        counts.rejected_rows = rejected.count()
        by_code = {r["code"]: r["count"] for r in rejected.groupBy("code").count().collect()}

        unique = keep(with_partitions(dedup.drop_exact_duplicates(valid).select(*columns), domain))
        counts.duplicate_rows = counts.valid_rows - unique.count()

        partitions = sorted("/".join(f"{name}={row[name]}" for name in PARTITION_COLUMNS)
                            for row in unique.select(*PARTITION_COLUMNS).distinct().collect())
        existing = self._existing(spark, plan, partitions, with_partitions)
        if existing is not None:
            existing = keep(existing)
            counts.existing_rows = existing.count()
            merged, already = dedup.merge_existing(unique, existing)
            counts.already_processed_rows = already.count()
        else:
            merged = unique
        merged_rows = counts.valid_rows - counts.duplicate_rows - counts.already_processed_rows + counts.existing_rows

        final = dedup.keep_latest_versions(merged).localCheckpoint(eager=True)
        cached.append(final)
        counts.output_rows = final.count()
        counts.superseded_rows = merged_rows - counts.output_rows
        mine = final.where(F.col("processing_job_id") == F.lit(str(job.job_id)))
        counts.new_rows = mine.count()

        if counts.output_rows:
            write_processed(final, domain, spark_path(self.store, plan.output_prefix))

        def release() -> None:
            for df in cached:
                df.unpersist()

        return EngineResult(
            counts=counts,
            output_partitions=partitions if counts.output_rows else [],
            rejects=(RejectRow(r["raw_key"], r["code"], r["message"], r["source"], r["envelope_event_id"])
                     for r in rejected.orderBy("raw_key").toLocalIterator()),
            rejects_by_code=by_code,
            new_event_ids=(r["event_id"] for r in mine.select("event_id").orderBy("event_id").toLocalIterator()),
            app_id=spark.sparkContext.applicationId,
            release=release,
        )

    def _existing(self, spark, plan: InputPlan, partitions: List[str], with_partitions):
        """Rows already processed in the partitions this job will rewrite (None when there are none)."""
        present = [p for p in partitions if any(True for _ in self._part_files(plan.output_prefix, p))]
        if not present:
            return None
        paths = [spark_path(self.store, f"{plan.output_prefix}{p}") for p in present]
        df = spark.read.schema(processed_struct(plan.domain)).parquet(*paths)
        return with_partitions(df, plan.domain)

    def _part_files(self, output_prefix: str, partition: str):
        for info in self.store.list(f"{output_prefix}{partition}/"):
            name = info.key.rsplit("/", 1)[-1]
            if name.startswith("part-") and name.endswith(".parquet"):
                yield info


def partition_counts(df) -> Dict[str, int]:
    """Rows per partition (diagnostics and tests)."""
    return {"/".join(f"{n}={row[n]}" for n in PARTITION_COLUMNS): row["count"]
            for row in df.groupBy(*PARTITION_COLUMNS).count().collect()}
