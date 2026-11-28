import pytest

from app.domain.enums import (
    RECORD_DELIVERED,
    RECORD_RETRYABLE,
    RECORD_TRANSITIONS,
    IngestionRecordStatus as S,
    IngestionRunStatus,
)
from app.pipeline.tracking import counter_violations, derive_run_status
from app.repositories.ingestion import RunCounters


def counters(**kw) -> RunCounters:
    return RunCounters(**kw)


@pytest.mark.parametrize(
    "c, fatal, expected",
    [
        (counters(), None, IngestionRunStatus.COMPLETED),
        (counters(received=3, valid=3, published=2, duplicate=1), None, IngestionRunStatus.COMPLETED),
        (counters(received=3, valid=2, invalid=1, published=2), None, IngestionRunStatus.PARTIAL),
        (counters(received=2, valid=2, published=1, publish_failed=1), None, IngestionRunStatus.PARTIAL),
        (counters(received=2, valid=2, duplicate=2), "adapter crashed", IngestionRunStatus.PARTIAL),
        (counters(received=2, invalid=2), None, IngestionRunStatus.FAILED),
        (counters(received=1, valid=1, publish_failed=1), None, IngestionRunStatus.FAILED),
        (counters(), "source unreachable", IngestionRunStatus.FAILED),
    ],
)
def test_derive_run_status(c, fatal, expected):
    assert derive_run_status(c, fatal) is expected


def test_counter_invariants():
    assert counter_violations(counters(received=3, valid=2, invalid=1, published=1, duplicate=1)) == []
    assert counter_violations(counters(received=3, valid=2)) != []
    assert counter_violations(counters(received=2, valid=2, published=1)) != []


def test_record_lifecycle_only_moves_forward():
    assert S.PENDING not in RECORD_TRANSITIONS
    for target, sources in RECORD_TRANSITIONS.items():
        assert target not in sources
    assert S.LOADED not in RECORD_TRANSITIONS[S.PROCESSED]
    assert RECORD_DELIVERED.isdisjoint(RECORD_RETRYABLE)
    assert S.DLQ in RECORD_RETRYABLE
