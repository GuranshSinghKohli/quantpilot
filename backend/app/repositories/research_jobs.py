"""Research jobs: idempotent creation per owner."""

from __future__ import annotations

import uuid
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ResearchJobStatus
from app.domain.models import ResearchJob
from app.domain.schemas import ResearchJobIn
from app.repositories._upsert import insert_ignore


class IdempotencyConflict(Exception):
    """The idempotency key was already used by this owner for a different request."""


def create_job(db: Session, data: ResearchJobIn) -> Tuple[ResearchJob, bool]:
    """Same owner + same key returns the original job. Reusing a key for a
    different request is a client error, not a silent overwrite."""
    created = insert_ignore(
        db,
        ResearchJob,
        {
            "id": uuid.uuid4(),
            "owner_key": data.owner_key,
            "job_type": data.job_type.value,
            "status": ResearchJobStatus.QUEUED.value,
            "idempotency_key": data.idempotency_key,
            "request": data.request,
            "event_id": data.event_id,
            "attempts": 0,
        },
        ["owner_key", "idempotency_key"],
    )
    job = db.scalars(
        select(ResearchJob).where(
            ResearchJob.owner_key == data.owner_key,
            ResearchJob.idempotency_key == data.idempotency_key,
        )
    ).one()
    if not created and (job.job_type != data.job_type.value or job.request != data.request):
        raise IdempotencyConflict(
            f"Idempotency key {data.idempotency_key!r} was used for a different request"
        )
    return job, created
