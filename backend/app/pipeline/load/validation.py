"""Validation of processed rows before anything is written.

Per row: required columns present, the source registered for this domain,
identities recomputed from the row itself (``event_id`` from
source|external_id|content_hash, ``dedup_key`` from the contract), the raw key
consistent with the event, the typed canonical row valid, and the domain rules
the canonical constraints rely on. Per batch, against Postgres: every
``event_id`` is a tracked ingestion record of this domain with the same raw
URI, and every ``processing_job_id`` is a processing job.

SYNTHETIC sources never reach canonical tables unless the caller explicitly
allows them, which the runner only permits on a test database.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ingestion_models import IngestionRecord, ProcessingJob
from app.domain.ingestion_schemas import _Row
from app.pipeline import identity
from app.pipeline.contracts import SOURCES, SUPPORTED_SCHEMA_VERSIONS
from app.pipeline.lake import InvalidKey
from app.pipeline.lake.keys import parse_raw_key
from app.pipeline.load.domains import DomainLoader
from app.pipeline.spark.schemas import processed_columns
from app.repositories._upsert import chunks

_BATCH = 500


def _pydantic_reason(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'row'}: {e['msg']}" for e in exc.errors())


def check_record(
    loader: DomainLoader, rec: Dict[str, Any], *, allow_synthetic: bool, raw_uri: Callable[[str], str]
) -> Tuple[Optional[_Row], List[str]]:
    """(typed row or None, problems) for one processed row. No I/O; ``raw_uri``
    maps a raw key to its URI in this lake (``ObjectStore.uri``)."""
    domain = loader.domain
    problems = [f"{c.name} is null" for c in processed_columns(domain) if c.required and rec.get(c.name) is None]
    if problems:
        return None, problems

    spec = SOURCES.get(rec["source"])
    if spec is None or spec.domain.value != domain:
        problems.append(f"source {rec['source']!r} is not a registered {domain} source")
    else:
        if bool(rec["synthetic"]) != spec.synthetic:
            problems.append(f"synthetic={rec['synthetic']} disagrees with source {spec.key} (synthetic={spec.synthetic})")
        if spec.synthetic and not allow_synthetic:
            problems.append(
                f"source {spec.key} is SYNTHETIC; synthetic rows never reach canonical tables "
                "(--allow-synthetic is accepted only on a test database)"
            )
    if rec["envelope_schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        problems.append(f"envelope_schema_version {rec['envelope_schema_version']} is not supported")
    expected_event = identity.event_id(rec["source"], rec["external_id"], rec["content_hash"])
    if rec["event_id"] != expected_event:
        problems.append("event_id does not match sha256(source|external_id|content_hash)")
    try:
        raw_domain, _, raw_event = parse_raw_key(rec["raw_key"])
        if (raw_domain, raw_event) != (domain, rec["event_id"]):
            problems.append(f"raw_key {rec['raw_key']} belongs to {raw_domain} event {raw_event}")
    except InvalidKey as exc:
        problems.append(str(exc))
    try:
        if rec["raw_uri"] != raw_uri(rec["raw_key"]):
            problems.append(f"raw_uri {rec['raw_uri']} does not point at raw_key {rec['raw_key']} in this lake")
    except InvalidKey as exc:
        problems.append(str(exc))
    try:
        uuid.UUID(rec["processing_job_id"])
    except (ValueError, TypeError, AttributeError):
        problems.append(f"processing_job_id {rec['processing_job_id']!r} is not a UUID")
    if domain == "company" and rec.get("cik") is not None and rec["external_id"] != rec["cik"]:
        problems.append(f"external_id {rec['external_id']} is not the CIK {rec['cik']}")

    try:
        row = loader.to_row(rec)
    except ValidationError as exc:
        return None, problems + [_pydantic_reason(exc)]
    problems += loader.problems(row)
    if rec["dedup_key"] != loader.dedup_key(row):
        problems.append(f"dedup_key {rec['dedup_key']} does not match the contract key {loader.dedup_key(row)}")
    return row, problems


def lineage_problems(db: Session, domain: str, rows: Sequence[_Row]) -> Dict[str, List[str]]:
    """event_id → problems for rows whose lineage targets are missing or disagree."""
    found: Dict[str, List[str]] = {}
    tracked: Dict[str, Tuple[str, Optional[str]]] = {}
    event_ids = [r.event_id for r in rows]
    for batch in chunks(event_ids, _BATCH):
        for eid, rec_domain, raw_uri in db.execute(
            select(IngestionRecord.event_id, IngestionRecord.domain, IngestionRecord.raw_uri).where(
                IngestionRecord.event_id.in_(batch)
            )
        ):
            tracked[eid] = (rec_domain, raw_uri)
    job_ids = sorted({r.processing_job_id for r in rows})
    jobs = set()
    for batch in chunks(job_ids, _BATCH):
        jobs.update(db.scalars(select(ProcessingJob.id).where(ProcessingJob.id.in_(batch))))
    for row in rows:
        problems = []
        record = tracked.get(row.event_id)
        if record is None:
            problems.append("event_id is not a tracked ingestion record")
        else:
            if record[0] != domain:
                problems.append(f"ingestion record is a {record[0]} event")
            if record[1] != row.raw_uri:
                problems.append(f"raw_uri differs from ingestion_records.raw_uri {record[1]!r}")
        if row.processing_job_id not in jobs:
            problems.append(f"processing_job_id {row.processing_job_id} is not a processing job")
        if problems:
            found.setdefault(row.event_id, []).extend(problems)
    return found


def synthetic_allowed(db: Session) -> bool:
    """Synthetic rows may be loaded only into a database whose name says it is a test database."""
    name = db.get_bind().url.database or ""
    return "test" in name.lower()
