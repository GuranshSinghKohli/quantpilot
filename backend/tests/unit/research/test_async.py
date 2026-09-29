"""Async research submission, outbox, worker, and read API. FakeLLM only."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.core.settings import get_settings
from app.domain.models import ResearchClaim, ResearchJob, ResearchOutbox
from app.research.metrics import RESEARCH_METRICS
from app.pipeline.kafka.producer import DeliveryFailed, OutboundMessage
from app.repositories.research_jobs import IdempotencyConflict
from app.research.llm import FakeLLMProvider
from app.research.outbox import QueueFull, claim_pending, publish_pending
from app.research.service import ResearchUnavailable
from app.research.submit import research_view, submit_research
from app.research.worker import claim_job, dispatch_due, renew_lease, run_once
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from app.routers.research import ResearchBody, create_research, read_research
from tests.unit.research.helpers import close_db, db_session, seed_partnership


class MemoryPublisher:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.sent: list[OutboundMessage] = []

    def publish(self, message: OutboundMessage):
        if self.failures:
            self.failures -= 1
            raise DeliveryFailed("timeout", "broker down", retriable=True)
        self.sent.append(message)
        return message


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def _request():
    return ResearchRequest(query="AMD partnership with Meta")


def _outbox_pending_gauge() -> float:
    return RESEARCH_METRICS.value(
        "research_outbox_pending",
        operation="outbox",
        status="pending",
        component="research",
    )


def _submit(db, key="key-1", query="AMD partnership with Meta"):
    return submit_research(
        db,
        ResearchRequest(query=query),
        owner_key="research-api",
        idempotency_key=key,
        correlation_id="corr-1",
        settings=get_settings(),
    )


def test_submit_is_durable_and_idempotent(db):
    job = _submit(db)
    assert job.status == "publish_pending"
    assert job.correlation_id == "corr-1"
    rows = db.query(ResearchOutbox).filter_by(research_job_id=job.id).all()
    assert len(rows) == 1 and rows[0].kind == "request" and rows[0].status == "pending"
    again = _submit(db)
    assert again.id == job.id
    assert db.query(ResearchOutbox).filter_by(research_job_id=job.id).count() == 1
    with pytest.raises(IdempotencyConflict):
        _submit(db, query="a different question")


def test_queue_limit_and_oversized(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "RESEARCH_MAX_QUEUED_JOBS", 1)
    _submit(db, key="one")
    with pytest.raises(QueueFull):
        _submit(db, key="two")
    with pytest.raises(HTTPException) as oversized:
        create_research(ResearchBody(query="AMD", max_documents=9999), db=db)
    assert oversized.value.status_code == 400


def test_publish_success_failure_and_retry(db):
    job = _submit(db)
    settings = get_settings()
    failing = MemoryPublisher(failures=1)
    publish_pending(
        db, failing, worker_id="pub", batch=10, lease_seconds=30,
        max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES, policy=_policy(),
    )
    assert job.status == "publish_pending"
    row = db.query(ResearchOutbox).filter_by(research_job_id=job.id).one()
    assert row.status == "pending" and row.attempts == 1
    assert _outbox_pending_gauge() == 1
    row.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    ok = MemoryPublisher()
    publish_pending(
        db, ok, worker_id="pub", batch=10, lease_seconds=30,
        max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES, policy=_policy(),
    )
    assert job.status == "queued"
    assert row.status == "published"
    assert ok.sent[0].key == str(job.id)
    assert _outbox_pending_gauge() == 0
    again = publish_pending(
        db, ok, worker_id="pub-2", batch=10, lease_seconds=30,
        max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES, policy=_policy(),
    )
    assert again == 0
    assert len(ok.sent) == 1


def test_second_publisher_does_not_take_a_lock(db):
    _submit(db)
    settings = get_settings()
    first = claim_pending(db, "a", limit=10, lease_seconds=60)
    second = claim_pending(db, "b", limit=10, lease_seconds=60)
    assert len(first) == 1 and second == []
    assert first[0].locked_by == "a"


def test_worker_success_duplicate_partial_and_failure(db, monkeypatch):
    world = seed_partnership(db)
    settings = get_settings()
    job = submit_research(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        owner_key="research-api",
        idempotency_key="run-1",
        correlation_id="corr-run",
        settings=settings,
    )
    job.status = "queued"
    event = db.query(ResearchOutbox).filter_by(research_job_id=job.id).one()
    body = json.dumps(event.payload).encode()
    status = run_once(
        db, body, settings, worker_id="w1",
        llm=FakeLLMProvider(), embed=FakeEmbeddingProvider(),
    )
    assert status == "succeeded"
    assert job.correlation_id == "corr-run"
    claims = db.query(ResearchClaim).filter_by(research_job_id=job.id).count()
    assert claims > 0
    assert run_once(db, body, settings, worker_id="w2", llm=FakeLLMProvider(), embed=FakeEmbeddingProvider()) == "duplicate"
    assert db.query(ResearchClaim).filter_by(research_job_id=job.id).count() == claims
    completed = [row for row in db.query(ResearchOutbox).filter_by(research_job_id=job.id) if row.kind == "completed"]
    assert completed and completed[0].payload["payload"]["status"] == "succeeded"

    partial_job = submit_research(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        owner_key="research-api",
        idempotency_key="run-partial",
        correlation_id="corr-partial",
        settings=settings,
    )
    partial_job.status = "queued"
    partial_event = db.query(ResearchOutbox).filter_by(research_job_id=partial_job.id).one()
    partial_status = run_once(
        db, json.dumps(partial_event.payload).encode(), settings, worker_id="w1",
        llm=FakeLLMProvider(fail_agents={"news"}), embed=FakeEmbeddingProvider(),
    )
    assert partial_status == "partial"
    assert partial_job.status == "partial"

    monkeypatch.setattr(settings, "RESEARCH_MAX_ATTEMPTS", 1)
    failed_job = submit_research(
        db,
        ResearchRequest(query="AMD partnership with Meta"),
        owner_key="research-api",
        idempotency_key="run-fail",
        correlation_id="corr-fail",
        settings=settings,
    )
    failed_job.status = "queued"
    failed_event = db.query(ResearchOutbox).filter_by(research_job_id=failed_job.id).one()

    def _down(*_args, **_kwargs):
        raise ResearchUnavailable("authentication failure", error_type="auth")

    monkeypatch.setattr("app.research.worker.run_research", _down)
    assert run_once(db, json.dumps(failed_event.payload).encode(), settings, worker_id="w1") == "failed"
    assert failed_job.status == "failed"
    assert any(row.kind == "dlq" for row in db.query(ResearchOutbox).filter_by(research_job_id=failed_job.id))


def test_retry_then_stale_lease(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "RESEARCH_MAX_ATTEMPTS", 3)
    job = _submit(db, key="retry")
    job.status = "queued"
    event = db.query(ResearchOutbox).one()
    calls = {"n": 0}

    def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ResearchUnavailable("timeout", error_type="timeout")
        from app.research.service import run_research
        return run_research(*args, **kwargs)

    monkeypatch.setattr("app.research.worker.run_research", _flaky)
    assert run_once(db, json.dumps(event.payload).encode(), settings, worker_id="w1", embed=FakeEmbeddingProvider()) == "queued"
    assert job.next_attempt_at is not None
    job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert dispatch_due(db, settings, worker_id="w2", embed=FakeEmbeddingProvider()) == 1
    assert job.status == "succeeded"

    owned = _submit(db, key="lease")
    owned.status = "queued"
    assert claim_job(db, owned, "owner", settings) is True
    assert claim_job(db, owned, "other", settings) is False
    assert renew_lease(db, owned.id, "owner", 30) is True
    owned.lease_until = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.flush()
    assert claim_job(db, owned, "other", settings) is True


def test_serve_commits_the_lease_before_execution(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.research.worker import serve

    settings = get_settings()
    monkeypatch.setattr(settings, "RESEARCH_HEARTBEAT_SECONDS", 30)
    job = _submit(db, key="serve-lease")
    job.status = "queued"
    body = json.dumps(db.query(ResearchOutbox).one().payload).encode()
    db.commit()
    Session = sessionmaker(bind=db.info["engine"])
    seen = {}

    def _spy(session, job_row, _settings, llm=None, embed=None):
        other = Session()
        try:
            stored = other.get(ResearchJob, job_row.id)
            seen["status"] = stored.status
            seen["worker"] = stored.worker_id
        finally:
            other.close()
        job_row.status = "succeeded"
        session.flush()
        return "succeeded"

    monkeypatch.setattr("app.research.worker.execute_job", _spy)

    class _Consumer:
        def __init__(self):
            self._sent = False
            self.committed = False

        def poll(self, _timeout):
            if self._sent:
                return None
            self._sent = True
            return self

        def value(self):
            return body

        def error(self):
            return None

        def commit(self, message=None, asynchronous=True):
            self.committed = True

    consumer = _Consumer()
    handled = serve(
        Session, consumer, MemoryPublisher(), settings,
        worker_id="w-serve", max_messages=1, idle_timeout_s=1,
    )
    assert handled == 1
    assert seen["status"] == "running"
    assert seen["worker"] == "w-serve"
    assert consumer.committed is True
    finished = Session()
    try:
        assert finished.get(ResearchJob, job.id).status == "succeeded"
    finally:
        finished.close()


def test_malformed_unknown_and_secrets(db):
    settings = get_settings()
    assert run_once(db, b"{not-json", settings, worker_id="w") == "dlq"
    missing = json.dumps({
        "schema_version": 1,
        "event_id": "a" * 64,
        "source": "quantpilot.research",
        "source_type": "research_job",
        "external_id": "missing",
        "occurred_at": "2026-09-28T00:00:00+00:00",
        "ingested_at": "2026-09-28T00:00:00+00:00",
        "content_hash": "b" * 64,
        "correlation_id": "c",
        "payload": {"kind": "request", "research_id": str(uuid.uuid4()), "request_version": "v1"},
    }).encode()
    assert run_once(db, missing, settings, worker_id="w") == "dlq"
    poisoned = b'{"schema_version":1,"api_key":"sk-supersecretvalue"}'
    assert run_once(db, poisoned, settings, worker_id="w") == "dlq"
    job = _submit(db, key="secret")
    job.error = "authorization: Bearer sk-supersecretvalue"
    view = research_view(db, job.id)
    assert "sk-" not in (view["error"] or "")
    assert "sql" not in json.dumps(view)


def test_api_statuses(db):
    accepted = create_research(
        ResearchBody(query="AMD partnership with Meta", idempotency_key="api-1"),
        db=db,
        correlation_id="corr-api",
    )
    assert accepted["status"] == "publish_pending"
    assert accepted["correlation_id"] == "corr-api"
    assert "report" not in accepted
    queued = read_research(uuid.UUID(accepted["research_id"]), db=db)
    assert queued["status"] == "publish_pending"
    job = db.get(ResearchJob, uuid.UUID(accepted["research_id"]))
    for status in ("queued", "running", "succeeded", "partial", "failed"):
        job.status = status
        assert read_research(job.id, db=db)["status"] == status
    with pytest.raises(HTTPException) as missing:
        read_research(uuid.uuid4(), db=db)
    assert missing.value.status_code == 404


def _policy():
    from app.pipeline.retry import RetryPolicy
    return RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.05, jitter=False)
