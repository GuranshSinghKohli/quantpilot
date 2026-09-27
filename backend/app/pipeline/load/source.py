"""Job → manifest → Parquet files. The only way the loader finds data.

The loader never lists ``processed/``: it opens exactly the files a COMPLETED
processing job's manifest names, after checking each one still exists with the
size the manifest recorded. Files are read one at a time with pyarrow and
streamed in record batches.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import IngestionDomain, ProcessingJobStatus
from app.domain.ingestion_models import ProcessingJob
from app.pipeline.lake import ObjectNotFound, ObjectStore
from app.pipeline.lake.keys import manifest_key, processed_prefix
from app.pipeline.load.errors import LoadConfigError, ManifestMismatch
from app.pipeline.spark.manifest import MANIFEST_VERSION
from app.pipeline.spark.schemas import PROCESSED_SCHEMA_VERSION, processed_columns

_PARTITION = re.compile(r"/year=(\d{4})/month=(\d{2})/day=(\d{2})/")
_MANIFEST_KEY = re.compile(r"(?:^|/)_manifests/processing/([0-9a-fA-F-]{36})\.json$")


@dataclass(frozen=True)
class ManifestFile:
    key: str
    size: int
    partition: str  # year=YYYY/month=MM/day=DD
    day: date


@dataclass(frozen=True)
class LoadSource:
    job_id: uuid.UUID
    domain: str
    manifest_uri: str
    files: Tuple[ManifestFile, ...]
    expected_rows: Optional[int]  # manifest output_rows; None when a date filter drops files

    @property
    def partitions(self) -> List[str]:
        return sorted({f.partition for f in self.files})


def parse_manifest_ref(ref: str) -> uuid.UUID:
    match = _MANIFEST_KEY.search(ref.strip())
    if not match:
        raise LoadConfigError(f"not a processing manifest key or URI: {ref!r} (expected …/_manifests/processing/<job_id>.json)")
    return uuid.UUID(match.group(1))


def check_selection(*, job_id=None, manifest=None, domain=None, latest=False) -> None:
    if sum([job_id is not None, manifest is not None, bool(latest)]) != 1:
        raise LoadConfigError("choose exactly one of --job, --manifest or --latest (the loader never loads the whole lake)")
    if latest and domain is None:
        raise LoadConfigError("--latest needs --domain (news, market, company or all)")
    if manifest is not None:
        parse_manifest_ref(manifest)


def select_jobs(
    db: Session,
    *,
    job_id: Optional[uuid.UUID] = None,
    manifest: Optional[str] = None,
    domain: Optional[str] = None,
    latest: bool = False,
) -> List[ProcessingJob]:
    """Exactly one of: a job id, a manifest reference, or ``latest`` for a domain
    ("all" = the latest job of each domain). There is no load-everything default."""
    check_selection(job_id=job_id, manifest=manifest, domain=domain, latest=latest)
    if manifest is not None:
        job_id = parse_manifest_ref(manifest)
    if job_id is not None:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            raise LoadConfigError(f"processing job {job_id} not found")
        if domain not in (None, "all") and job.domain != domain:
            raise LoadConfigError(f"processing job {job_id} is a {job.domain} job, not {domain}")
        return [_loadable(job)]
    domains = [d.value for d in IngestionDomain] if domain == "all" else [domain]
    jobs = []
    for d in domains:
        if d not in {x.value for x in IngestionDomain}:
            raise LoadConfigError(f"unknown domain {d!r}")
        job = db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.domain == d, ProcessingJob.status == ProcessingJobStatus.COMPLETED.value)
            .order_by(ProcessingJob.started_at.desc(), ProcessingJob.id.desc())
            .limit(1)
        ).first()
        if job is None:
            if domain == "all":
                continue
            raise LoadConfigError(f"no COMPLETED {d} processing job to load")
        jobs.append(job)
    if not jobs:
        raise LoadConfigError("no COMPLETED processing jobs to load")
    return jobs


def _loadable(job: ProcessingJob) -> ProcessingJob:
    if job.status != ProcessingJobStatus.COMPLETED.value:
        raise LoadConfigError(f"processing job {job.id} is {job.status}; only COMPLETED jobs can be loaded")
    return job


def open_source(
    store: ObjectStore, job: ProcessingJob, *, since: Optional[date] = None, until: Optional[date] = None
) -> LoadSource:
    """Read and check the job's manifest; return the files to load."""
    _loadable(job)
    key = manifest_key(job.id)
    uri = store.uri(key)
    if job.manifest_uri != uri:
        raise ManifestMismatch(f"processing job {job.id} records manifest {job.manifest_uri!r}, this lake has {uri!r}")
    try:
        document = json.loads(store.get(key))
    except ObjectNotFound as exc:
        raise ManifestMismatch(f"manifest {uri} is missing") from exc
    _check_manifest(document, job)

    prefix = processed_prefix(job.domain)
    files: List[ManifestFile] = []
    for entry in document["output"]["files"]:
        file_key, size = entry["key"], int(entry["size"])
        match = _PARTITION.search("/" + file_key)
        if not file_key.startswith(prefix) or not match or not file_key.endswith(".parquet"):
            raise ManifestMismatch(f"manifest file {file_key!r} is not a {job.domain} Parquet file under {prefix}")
        day = date(*(int(g) for g in match.groups()))
        files.append(ManifestFile(file_key, size, f"year={match.group(1)}/month={match.group(2)}/day={match.group(3)}", day))

    selected = [f for f in files if (since is None or f.day >= since) and (until is None or f.day <= until)]
    missing, changed = [], []
    for f in selected:
        info = store.head(f.key)
        if info is None:
            missing.append(f.key)
        elif info.size != f.size:
            changed.append(f"{f.key} ({info.size} bytes, manifest says {f.size})")
    if missing or changed:
        raise ManifestMismatch(
            f"processing job {job.id}'s files no longer match its manifest (missing: {missing or 'none'}; "
            f"changed: {changed or 'none'}). A later processing job rewrote these partitions: "
            f"load that job instead (python -m app.pipeline load --domain {job.domain} --latest)."
        )
    filtered = len(selected) != len(files)
    expected = None if filtered else int(document["counts"]["output_rows"])
    return LoadSource(job.id, job.domain, uri, tuple(selected), expected)


def _check_manifest(document: Dict[str, Any], job: ProcessingJob) -> None:
    problems = []
    expect = {
        "manifest_version": MANIFEST_VERSION,
        "kind": "processing",
        "job_id": str(job.id),
        "domain": job.domain,
        "status": ProcessingJobStatus.COMPLETED.value,
    }
    for field, value in expect.items():
        if document.get(field) != value:
            problems.append(f"{field}={document.get(field)!r} (expected {value!r})")
    schema = document.get("schema") or {}
    if schema.get("processed_schema_version") != PROCESSED_SCHEMA_VERSION:
        problems.append(
            f"processed_schema_version={schema.get('processed_schema_version')!r} (loader supports {PROCESSED_SCHEMA_VERSION})"
        )
    output = document.get("output") or {}
    if output.get("format") != "parquet" or not isinstance(output.get("files"), list):
        problems.append("output is not a list of Parquet files")
    if not isinstance((document.get("counts") or {}).get("output_rows"), int):
        problems.append("counts.output_rows missing")
    if problems:
        raise ManifestMismatch(f"manifest of processing job {job.id} is not loadable: " + "; ".join(problems))


# --- Parquet --------------------------------------------------------------------


def _arrow_ok(kind: str, dtype: pa.DataType) -> bool:
    if kind == "string":
        return pa.types.is_string(dtype) or pa.types.is_large_string(dtype)
    if kind == "int":
        return pa.types.is_int32(dtype)
    if kind == "bigint":
        return pa.types.is_int64(dtype)
    if kind == "boolean":
        return pa.types.is_boolean(dtype)
    if kind == "date":
        return pa.types.is_date32(dtype)
    if kind == "timestamp":
        return pa.types.is_timestamp(dtype) and dtype.tz in ("UTC", "+00:00", "Etc/UTC")
    if kind == "decimal(20,6)":
        return pa.types.is_decimal(dtype) and (dtype.precision, dtype.scale) == (20, 6)
    if kind == "array<string>":
        return (pa.types.is_list(dtype) or pa.types.is_large_list(dtype)) and _arrow_ok("string", dtype.value_type)
    raise ValueError(f"unknown column type {kind}")


def schema_problems(domain: str, schema: pa.Schema) -> List[str]:
    """Differences between a Parquet file's schema and the processed schema."""
    expected = {c.name: c.type for c in processed_columns(domain)}
    actual = {f.name: f.type for f in schema}
    problems = [f"missing column {n}" for n in expected if n not in actual]
    problems += [f"unexpected column {n}" for n in actual if n not in expected]
    problems += [
        f"column {n} is {actual[n]}, expected {kind}"
        for n, kind in expected.items()
        if n in actual and not _arrow_ok(kind, actual[n])
    ]
    return problems


def open_parquet(store: ObjectStore, file: ManifestFile) -> pq.ParquetFile:
    data = store.get(file.key)
    if len(data) != file.size:
        raise ManifestMismatch(f"{file.key} is {len(data)} bytes, manifest says {file.size}")
    return pq.ParquetFile(io.BytesIO(data))


def iter_rows(parquet: pq.ParquetFile, batch_size: int) -> Iterator[Sequence[Dict[str, Any]]]:
    for batch in parquet.iter_batches(batch_size=batch_size):
        yield batch.to_pylist()
