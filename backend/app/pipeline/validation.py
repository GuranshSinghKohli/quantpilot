"""Integrity checks that structure alone can't express.

``decode_event`` proves a message is shaped like an envelope. ``check_event``
proves it is internally consistent: identities recompute to the same values,
the source is registered for this domain, and timestamps are plausible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from app.pipeline import identity
from app.pipeline.contracts import SOURCES, SUPPORTED_SCHEMA_VERSIONS
from app.pipeline.errors import FailureReason, PermanentError
from app.pipeline.serde import Event

MAX_CLOCK_SKEW = timedelta(minutes=5)
EARLIEST_PLAUSIBLE = datetime(1970, 1, 1, tzinfo=timezone.utc)


def check_event(event: Event, now: Optional[Callable[[], datetime]] = None) -> None:
    """Raise ``PermanentError`` on the first class of problem found."""
    current = (now or (lambda: datetime.now(timezone.utc)))()
    record = event.record

    if event.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise PermanentError(FailureReason.UNSUPPORTED_SCHEMA_VERSION, f"schema_version {event.schema_version}")

    recomputed = record.compute_content_hash()
    if recomputed != record.content_hash or event.content_hash != record.content_hash:
        raise PermanentError(
            FailureReason.HASH_MISMATCH,
            "content_hash does not match the record",
            [{"envelope": event.content_hash, "record": record.content_hash, "recomputed": recomputed}],
        )

    problems: List[dict] = []
    spec = SOURCES.get(event.source)
    if spec is None:
        problems.append({"field": "source", "msg": f"unknown source {event.source!r}"})
    else:
        if spec.domain.value != event.domain:
            problems.append({"field": "domain", "msg": f"{event.source} produces {spec.domain}, not {event.domain}"})
        if spec.source_type != event.source_type:
            problems.append({"field": "source_type", "msg": f"expected {spec.source_type}"})
    if record.source != event.source:
        problems.append({"field": "record.source", "msg": "differs from envelope source"})

    expected = {
        "external_id": record.logical_external_id,
        "dedup_key": record.dedup_key,
        "event_id": identity.event_id(event.source, event.external_id, event.content_hash),
    }
    for field, value in expected.items():
        if getattr(event, field) != value:
            problems.append({"field": field, "msg": "does not match the value derived from the record"})

    if event.ingested_at > current + MAX_CLOCK_SKEW:
        problems.append({"field": "ingested_at", "msg": "in the future"})
    if event.occurred_at is not None:
        if event.occurred_at < EARLIEST_PLAUSIBLE:
            problems.append({"field": "occurred_at", "msg": "before 1970"})
        if event.occurred_at > event.ingested_at + MAX_CLOCK_SKEW:
            problems.append({"field": "occurred_at", "msg": "after ingested_at"})

    if problems:
        raise PermanentError(FailureReason.ENVELOPE_INVALID, f"{len(problems)} integrity problem(s)", problems)
