"""Spark processing: raw lake objects → validated, deduplicated, partitioned Parquet.

Pure modules (no pyspark import; usable without a JVM): ``config``, ``plan``,
``schemas`` (column specs), ``quality`` (check catalogue), ``manifest`` and
``jobs.runner``. Spark modules (need pyspark + Java 17): ``session``,
``readers``, ``transformers``, ``dedup``, ``writers.write_processed`` and
``jobs.engine``. Nothing here is imported by the API; Spark starts only from
``python -m app.pipeline process``.
"""
