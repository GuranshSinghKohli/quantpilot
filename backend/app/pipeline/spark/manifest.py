"""Processing manifests: one write-once JSON document per job at ``_manifests/processing/<job_id>.json``.

A manifest says which raw objects a job read, which partitions and Parquet files
it left behind, what it rejected and why, the counts, the output schema and the
engine that ran it. Loaders read the manifest of a COMPLETED job (processing_jobs
is authoritative for status); a manifest is not a data catalog.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app.pipeline.lake import IntegrityError, ObjectInfo, ObjectStore
from app.pipeline.lake.keys import manifest_key
from app.pipeline.spark.plan import InputPlan
from app.pipeline.spark.quality import describe_checks
from app.pipeline.spark.schemas import (
    PARTITION_BASIS,
    PARTITION_COLUMNS,
    PROCESSED_SCHEMA_VERSION,
    SORT_KEYS,
    processed_columns,
)

MANIFEST_VERSION = 1


def build_manifest(
    *,
    store: ObjectStore,
    job_id: uuid.UUID,
    job_name: str,
    plan: InputPlan,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    counts: Dict[str, int],
    partitions: Sequence[str] = (),
    files: Sequence[ObjectInfo] = (),
    rejects: Optional[Dict[str, Any]] = None,
    engine: Optional[Dict[str, Any]] = None,
    code_version: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    from app.pipeline.contracts import SUPPORTED_SCHEMA_VERSIONS

    rejects = rejects or {}
    by_code = rejects.get("by_code", {})
    return {
        "manifest_version": MANIFEST_VERSION,
        "kind": "processing",
        "job_id": str(job_id),
        "job_name": job_name,
        "domain": plan.domain,
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "code_version": code_version,
        "engine": engine or {},
        "store": {"backend": store.backend, "uri_prefix": _uri_prefix(store)},
        "input": {**plan.describe(), "objects": list(plan.keys), "ignored": list(plan.ignored)},
        "output": {
            "format": "parquet",
            "uri": store.uri(plan.output_prefix.rstrip("/")),
            "prefix": plan.output_prefix,
            "partitioning": list(PARTITION_COLUMNS),
            "partition_basis": PARTITION_BASIS[plan.domain],
            "sort_keys": list(SORT_KEYS[plan.domain]),
            "partitions": list(partitions),
            "files": [{"key": f.key, "uri": store.uri(f.key), "size": f.size} for f in files],
        },
        "rejects": {
            "count": rejects.get("count", 0),
            "written": rejects.get("written", 0),
            "already_recorded": rejects.get("already_recorded", 0),
            "by_code": dict(sorted(by_code.items())),
            "checks": describe_checks(sorted(by_code)),
            "keys": list(rejects.get("keys", [])),
        },
        "counts": counts,
        "schema": {
            "processed_schema_version": PROCESSED_SCHEMA_VERSION,
            "envelope_schema_versions": sorted(SUPPORTED_SCHEMA_VERSIONS),
            "columns": [c.as_dict() for c in processed_columns(plan.domain)],
        },
        "error": error,
    }


def write_manifest(store: ObjectStore, job_id: uuid.UUID, document: Dict[str, Any]) -> str:
    key = manifest_key(job_id)
    put = store.put_if_absent(key, json.dumps(document, indent=2, sort_keys=True).encode("utf-8"),
                              content_type="application/json")
    if not put.created:
        raise IntegrityError(f"{put.uri} already exists; job ids are unique, so this is a second writer")
    return put.uri


def _uri_prefix(store: ObjectStore) -> str:
    from app.pipeline.spark.plan import PlanError, uri_prefix

    try:
        return uri_prefix(store)[0]
    except PlanError:
        return store.backend


def read_manifest(store: ObjectStore, job_id: uuid.UUID) -> Dict[str, Any]:
    return json.loads(store.get(manifest_key(job_id)))


def manifest_files(document: Dict[str, Any]) -> List[str]:
    return [f["key"] for f in document["output"]["files"]]
