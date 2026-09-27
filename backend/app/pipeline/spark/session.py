"""SparkSession factory. Called only by the processing CLI and the spark test tier."""

from __future__ import annotations

from app.pipeline.spark.config import ProcessingConfigError, SparkConfig

_S3A = "org.apache.hadoop.fs.s3a.S3AFileSystem"


def build_session(config: SparkConfig):
    from pyspark.sql import SparkSession

    builder = SparkSession.builder
    for key, value in config.conf().items():
        builder = builder.config(key, value)
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel(config.log_level)
    if config.lake_backend == "s3":
        require_s3a(spark)
    return spark


def require_s3a(spark) -> None:
    """Fail up front, with the fix, when the S3A jars are not on the classpath."""
    try:
        spark.sparkContext._jvm.java.lang.Class.forName(_S3A)
    except Exception as exc:  # py4j wraps ClassNotFoundException
        raise ProcessingConfigError(
            "LAKE_BACKEND=s3 needs the S3A jars (hadoop-aws + AWS SDK bundle); run processing in the "
            "spark image (`make spark-*`) or use LAKE_BACKEND=local"
        ) from exc
