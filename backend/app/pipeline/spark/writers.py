"""Outputs of a processing job: processed Parquet (Spark), reject objects and output listing (ObjectStore).

Processed partitions are rewritten with dynamic partition overwrite: only the
partitions present in the job's output are replaced, each with the complete,
deduplicated contents of that partition (existing rows are merged in before the
write). Raw objects are never written. Reject objects are write-once through
the ObjectStore: an existing reject object is kept, not replaced.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from app.pipeline.lake import ObjectInfo, ObjectStore
from app.pipeline.lake.keys import parse_raw_key, reject_key
from app.pipeline.spark.quality import CHECKS
from app.pipeline.spark.schemas import PARTITION_COLUMNS, SORT_KEYS

REJECT_VERSION = 1


def write_processed(df, domain: str, output_uri: str) -> None:
    """One file per partition (more past spark.sql.files.maxRecordsPerFile), rows in SORT_KEYS order."""
    (
        df.repartition(*PARTITION_COLUMNS)
        .sortWithinPartitions(*PARTITION_COLUMNS, *SORT_KEYS[domain])
        .write.mode("overwrite")
        .partitionBy(*PARTITION_COLUMNS)
        .parquet(output_uri)
    )


@dataclass(frozen=True)
class RejectRow:
    raw_key: str
    code: str
    message: str
    source: Optional[str] = None
    envelope_event_id: Optional[str] = None


@dataclass
class RejectSummary:
    written: int = 0
    already_recorded: int = 0
    keys: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.written + self.already_recorded


def write_rejects(store: ObjectStore, rows: Iterable[RejectRow], *, job_id: uuid.UUID, processed_at: datetime) -> RejectSummary:
    summary = RejectSummary()
    for row in rows:
        domain, day, event_id = parse_raw_key(row.raw_key)
        key = reject_key(domain, day, event_id)
        document = {
            "reject_version": REJECT_VERSION,
            "event_id": event_id,
            "envelope_event_id": row.envelope_event_id,
            "source": row.source,
            "domain": domain,
            "processing_job_id": str(job_id),
            "error_code": row.code,
            "error_message": row.message,
            "check": CHECKS[row.code].description,
            "raw_key": row.raw_key,
            "raw_uri": store.uri(row.raw_key),
            "processed_at": processed_at.isoformat(),
        }
        put = store.put_if_absent(key, json.dumps(document, sort_keys=True).encode("utf-8"), content_type="application/json")
        if put.created:
            summary.written += 1
        else:
            summary.already_recorded += 1
        summary.keys.append(key)
    return summary


def output_files(store: ObjectStore, output_prefix: str, partitions: Sequence[str]) -> List[ObjectInfo]:
    """Parquet part files currently in the job's output partitions."""
    files: List[ObjectInfo] = []
    for partition in partitions:
        for info in store.list(f"{output_prefix}{partition}/"):
            name = info.key.rsplit("/", 1)[-1]
            if name.startswith("part-") and name.endswith(".parquet"):
                files.append(info)
    return files
