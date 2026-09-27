"""Idempotency and integrity of core-domain writes on PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.exc import IntegrityError

from app.domain.enums import (
    EventEntityRole,
    EventType,
    EvidenceStance,
    RelationshipStatus,
    RelationshipType,
    ResearchJobType,
    SourceType,
)
from app.domain.models import Entity, Relationship, Source
from app.domain.schemas import (
    CompanyIn,
    EventEntityIn,
    EventIn,
    EvidenceIn,
    RelationshipEvidenceIn,
    RelationshipIn,
    ResearchJobIn,
    SourceIn,
    TechnologyIn,
)
from app.repositories import entities, events, relationships, research_jobs, sources

NOW = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)


def _source(url: str = "https://Example.com/news/nvda-tsmc/?utm_source=x", text: str = "Body") -> SourceIn:
    return SourceIn(source_type=SourceType.NEWS, url=url, retrieved_at=NOW, content_text=text)


def _evidence(excerpt: str = "NVIDIA expands its packaging agreement with TSMC.") -> EvidenceIn:
    return EvidenceIn(excerpt=excerpt, extraction_method="test", extracted_at=NOW)


def test_source_dedups_on_normalized_url(db):
    first, created = sources.upsert_source(db, _source())
    again, created_again = sources.upsert_source(
        db, _source("https://example.com/news/nvda-tsmc#section-2")
    )
    assert created and not created_again
    assert again.id == first.id
    assert first.url == "https://example.com/news/nvda-tsmc"


def test_evidence_dedups_on_whitespace_insensitive_content(db):
    source, _ = sources.upsert_source(db, _source())
    a, created = sources.add_evidence(db, source.id, _evidence())
    b, created_again = sources.add_evidence(
        db, source.id, _evidence("NVIDIA  expands its packaging\nagreement with TSMC.")
    )
    assert created and not created_again
    assert a.id == b.id


def test_source_with_evidence_cannot_be_deleted(db):
    source, _ = sources.upsert_source(db, _source())
    sources.add_evidence(db, source.id, _evidence())
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(delete(Source).where(Source.id == source.id))


def test_company_resolves_by_cik_then_ticker(db):
    created_co, created = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA Corporation", ticker="nvda", exchange="nasdaq", cik="1045810")
    )
    by_cik, c1 = entities.get_or_create_company(db, CompanyIn(legal_name="Nvidia Corp", cik="0001045810"))
    by_ticker, c2 = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA", ticker="NVDA", exchange="NASDAQ")
    )
    assert created and not c1 and not c2
    assert by_cik.entity_id == by_ticker.entity_id == created_co.entity_id
    assert created_co.entity.entity_type == "company"


def test_technology_is_unique_by_slug(db):
    a, created = entities.get_or_create_technology(db, TechnologyIn(name="CoWoS packaging", slug="cowos"))
    b, created_again = entities.get_or_create_technology(db, TechnologyIn(name="CoWoS", slug="cowos"))
    assert created and not created_again and a.entity_id == b.entity_id


def _two_companies(db):
    nvda, _ = entities.get_or_create_company(db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA"))
    tsm, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Taiwan Semiconductor", ticker="TSM"))
    return nvda.entity_id, tsm.entity_id


def test_relationship_upsert_extends_seen_window(db):
    nvda, tsm = _two_companies(db)
    edge, created = relationships.upsert_relationship(
        db, RelationshipIn(subject_entity_id=tsm, relationship_type=RelationshipType.SUPPLIES, object_entity_id=nvda, observed_at=NOW)
    )
    later = NOW + timedelta(days=10)
    same, created_again = relationships.upsert_relationship(
        db, RelationshipIn(subject_entity_id=tsm, relationship_type=RelationshipType.SUPPLIES, object_entity_id=nvda, observed_at=later)
    )
    assert created and not created_again and same.id == edge.id
    assert same.first_seen_at == NOW and same.last_seen_at == later
    assert same.status == RelationshipStatus.ASSERTED.value


def test_relationship_status_follows_evidence(db):
    nvda, tsm = _two_companies(db)
    edge, _ = relationships.upsert_relationship(
        db, RelationshipIn(subject_entity_id=tsm, relationship_type=RelationshipType.SUPPLIES, object_entity_id=nvda, observed_at=NOW)
    )
    source, _ = sources.upsert_source(db, _source())
    pro, _ = sources.add_evidence(db, source.id, _evidence())
    con, _ = sources.add_evidence(db, source.id, _evidence("A second supplier now handles the packaging."))

    edge, _ = relationships.link_evidence(db, edge.id, RelationshipEvidenceIn(evidence_id=pro.id, stance=EvidenceStance.SUPPORTS))
    assert edge.status == RelationshipStatus.SUPPORTED.value
    edge, linked_again = relationships.link_evidence(db, edge.id, RelationshipEvidenceIn(evidence_id=pro.id, stance=EvidenceStance.SUPPORTS))
    assert not linked_again
    edge, _ = relationships.link_evidence(db, edge.id, RelationshipEvidenceIn(evidence_id=con.id, stance=EvidenceStance.CONTRADICTS))
    assert edge.status == RelationshipStatus.DISPUTED.value


def test_database_rejects_self_relationship_and_unknown_type(db):
    entity = Entity(id=uuid.uuid4(), entity_type="company", canonical_name="Solo Inc", normalized_name="solo inc")
    db.add(entity)
    db.flush()
    base = {"status": "asserted", "first_seen_at": NOW, "last_seen_at": NOW}
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(insert(Relationship).values(id=uuid.uuid4(), subject_entity_id=entity.id, object_entity_id=entity.id, relationship_type="SUPPLIES", **base))
    other = Entity(id=uuid.uuid4(), entity_type="company", canonical_name="Other Inc", normalized_name="other inc")
    db.add(other)
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(insert(Relationship).values(id=uuid.uuid4(), subject_entity_id=entity.id, object_entity_id=other.id, relationship_type="LIKES", **base))


def test_event_dedups_same_subject_day_and_source(db):
    nvda, tsm = _two_companies(db)
    source, _ = sources.upsert_source(db, _source())
    payload = dict(
        event_type=EventType.PARTNERSHIP,
        title="NVIDIA and TSMC expand packaging agreement",
        detected_at=NOW,
        occurred_at=NOW,
        primary_source_id=source.id,
    )
    first, created = events.upsert_event(
        db, EventIn(**payload, entities=[EventEntityIn(entity_id=nvda, role=EventEntityRole.SUBJECT)])
    )
    second, created_again = events.upsert_event(
        db,
        EventIn(
            **{**payload, "occurred_at": NOW + timedelta(hours=3)},
            entities=[
                EventEntityIn(entity_id=nvda, role=EventEntityRole.SUBJECT),
                EventEntityIn(entity_id=tsm, role=EventEntityRole.COUNTERPARTY),
            ],
        ),
    )
    assert created and not created_again and first.id == second.id
    db.refresh(second)
    assert {(e.entity_id, e.role) for e in second.entities} == {
        (nvda, "subject"),
        (tsm, "counterparty"),
    }


def test_research_job_idempotency_is_per_owner(db):
    body = ResearchJobIn(owner_key="user:1", job_type=ResearchJobType.EVENT_RESEARCH, idempotency_key="k-1", request={"q": "NVDA packaging"})
    job, created = research_jobs.create_job(db, body)
    same, created_again = research_jobs.create_job(db, body)
    assert created and not created_again and same.id == job.id
    assert job.status == "queued" and job.attempts == 0

    other_owner, created_other = research_jobs.create_job(db, body.model_copy(update={"owner_key": "user:2"}))
    assert created_other and other_owner.id != job.id

    with pytest.raises(research_jobs.IdempotencyConflict):
        research_jobs.create_job(db, body.model_copy(update={"request": {"q": "something else"}}))
