"""SparkSession configuration derived from Settings. Pure: builds a dict, starts nothing.

S3A credentials are never placed in the Spark conf (it shows up in logs, the UI
and event logs). S3A's default provider chain reads the standard AWS_* variables
or an instance role, the same sources boto3 uses for the raw lake.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Dict, Optional
from urllib.parse import urlsplit

from app.pipeline.errors import PipelineError


class ProcessingConfigError(PipelineError, ValueError):
    """Bad processing input or configuration; raised before any job row or Spark session exists."""


@dataclass(frozen=True)
class SparkConfig:
    app_name: str
    master: str
    shuffle_partitions: int
    log_level: str
    driver_memory: str
    max_records_per_file: int
    lake_backend: str
    endpoint_url: str = ""
    region: str = "us-east-1"

    @classmethod
    def from_settings(cls, settings) -> "SparkConfig":
        return cls(
            app_name=settings.SPARK_APP_NAME,
            master=settings.SPARK_MASTER,
            shuffle_partitions=settings.SPARK_SHUFFLE_PARTITIONS,
            log_level=settings.SPARK_LOG_LEVEL,
            driver_memory=settings.SPARK_DRIVER_MEMORY,
            max_records_per_file=settings.SPARK_MAX_RECORDS_PER_FILE,
            lake_backend=settings.LAKE_BACKEND,
            endpoint_url=settings.LAKE_ENDPOINT_URL,
            region=settings.LAKE_REGION,
        )

    def conf(self) -> Dict[str, str]:
        conf = {
            "spark.app.name": self.app_name,
            "spark.master": self.master,
            "spark.driver.memory": self.driver_memory,
            "spark.sql.shuffle.partitions": str(self.shuffle_partitions),
            "spark.sql.files.maxRecordsPerFile": str(self.max_records_per_file),
            # Every timestamp is an instant; the session never applies a local zone.
            "spark.sql.session.timeZone": "UTC",
            "spark.driver.extraJavaOptions": "-Duser.timezone=UTC",
            # Casts return null instead of raising, so the quality checks can name the bad field.
            "spark.sql.ansi.enabled": "false",
            # Overwrite only the partitions a job writes; see writers.write_processed.
            "spark.sql.sources.partitionOverwriteMode": "dynamic",
            # Partition values stay the zero-padded strings of the key layout.
            "spark.sql.sources.partitionColumnTypeInference.enabled": "false",
            "spark.sql.parquet.outputTimestampType": "TIMESTAMP_MICROS",
            "spark.ui.enabled": "false",
            "spark.ui.showConsoleProgress": "false",
        }
        if self.lake_backend == "s3":
            conf.update(self.s3a_conf())
        return conf

    def s3a_conf(self) -> Dict[str, str]:
        conf = {
            "spark.hadoop.fs.s3a.impl": "org.apache.hadoop.fs.s3a.S3AFileSystem",
            "spark.hadoop.fs.s3a.endpoint.region": self.region,
        }
        if self.endpoint_url:  # MinIO or another S3-compatible endpoint
            conf.update({
                "spark.hadoop.fs.s3a.endpoint": self.endpoint_url,
                "spark.hadoop.fs.s3a.path.style.access": "true",
                "spark.hadoop.fs.s3a.connection.ssl.enabled": str(urlsplit(self.endpoint_url).scheme == "https").lower(),
            })
        return conf


def spark_version() -> Optional[str]:
    """Installed pyspark version, read without starting a JVM."""
    try:
        return metadata.version("pyspark")
    except metadata.PackageNotFoundError:
        return None
