"""Event identity, candidate lifecycle, and resolver-backed associations."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.domain.enums import CandidateStatus, EventEntityRole, EventStatus, EventType
from app.domain.identity import event_evidence_identity, event_source_identity
from app.domain.ingestion_models import NewsArticle
from app.domain.models import Event, EventCandidate, EventEntity
from app.domain.schemas import CompanyIn
from app.graph.errors import AmbiguousEntity, ProvenanceRequired, UnresolvedEntity
from app.graph.resolve import EntityRef
from app.market.candidates import EventAssociationIn, EventCandidateProposal, EventCandidateService, propose_news_candidates
from app.repositories import entities
from tests.unit.market.helpers import close_db, company, evidence, migrated_db, seed_news, source


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_source_identity_ignores_title():
    a = event_source_identity("massive.news", "abc", "news")
    b = event_source_identity("massive.news", "abc", "news")
    c = event_source_identity("massive.news", "xyz", "news")
    assert a == b != c
    assert event_evidence_identity(uuid.UUID(int=1), uuid.UUID(int=2), "news") == event_evidence_identity(
        uuid.UUID(int=1), uuid.UUID(int=2), "news"
    )


def test_news_without_tickers_stays_a_document(db):
    seed_news(db, tickers=[], external_id="doc-only", url="https://example.com/doc")
    report = propose_news_candidates(db)
    assert report["created"] == 0
    assert db.scalar(select(EventCandidate)) is None
    assert db.scalar(select(NewsArticle)) is not None


def test_news_with_tickers_is_pending_and_idempotent(db):
    company(db, "NVIDIA Corporation", "NVDA", "1045810")
    seed_news(db, tickers=["NVDA", "ACME"])
    first = propose_news_candidates(db)
    second = propose_news_candidates(db)
    assert first["created"] == 1
    assert second["created"] == 0
    assert db.scalars(select(EventCandidate)).all().__len__() == 1
    row = db.scalars(select(EventCandidate)).one()
    assert row.status == CandidateStatus.PENDING.value
    assert row.event_type == EventType.NEWS.value
    assert db.scalar(select(Event)) is None


def test_accept_resolves_first_ticker_as_subject(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    seed_news(db, tickers=["NVDA", "ZZZZ"])
    propose_news_candidates(db)
    candidate = db.scalars(select(EventCandidate)).one()
    service = EventCandidateService()
    row, event = service.accept(db, candidate.id, tickers=["NVDA", "ZZZZ"])
    again, event2 = service.accept(db, candidate.id)
    assert row.status == CandidateStatus.ACCEPTED.value
    assert event.id == event2.id
    assert event.status == EventStatus.CONFIRMED.value
    links = list(db.scalars(select(EventEntity).where(EventEntity.event_id == event.id)))
    assert {(link.entity_id, link.role) for link in links} == {(nvda.entity_id, "subject")}


def test_unresolved_and_ambiguous_tickers_are_not_guessed(db):
    entities.get_or_create_company(db, CompanyIn(legal_name="Alpha One", ticker="DUP", cik="0000000011"))
    entities.get_or_create_company(db, CompanyIn(legal_name="Alpha Two", ticker="DUP", cik="0000000012", exchange="NYSE"))
    seed_news(db, tickers=["DUP", "NOSUCH"])
    propose_news_candidates(db)
    candidate = db.scalars(select(EventCandidate)).one()
    _, event = EventCandidateService().accept(db, candidate.id, tickers=["DUP", "NOSUCH"])
    assert list(db.scalars(select(EventEntity).where(EventEntity.event_id == event.id))) == []


def test_explicit_roles_require_resolved_entities(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    src = source(db)
    ev = evidence(db, src.id, "NVIDIA acquires Missing Co.")
    service = EventCandidateService()
    candidate, _ = service.propose(
        db,
        EventCandidateProposal(
            event_type=EventType.ACQUISITION,
            title="NVIDIA acquires Missing Co",
            source_id=src.id,
            evidence_id=ev.id,
        ),
    )
    with pytest.raises(UnresolvedEntity):
        service.accept(
            db,
            candidate.id,
            associations=[
                EventAssociationIn(entity=EntityRef(entity_id=nvda.entity_id), role=EventEntityRole.ACQUIRER),
                EventAssociationIn(entity=EntityRef(ticker="NOSUCH"), role=EventEntityRole.ACQUIRED),
            ],
        )


def test_rejected_candidate_never_becomes_an_event(db):
    src = source(db)
    ev = evidence(db, src.id)
    service = EventCandidateService()
    candidate, created = service.propose(
        db,
        EventCandidateProposal(
            event_type=EventType.NEWS,
            title="Ignore me",
            source_id=src.id,
            evidence_id=ev.id,
        ),
    )
    assert created
    service.reject(db, candidate.id)
    with pytest.raises(ProvenanceRequired):
        service.accept(db, candidate.id)
    assert db.scalar(select(Event)) is None


def test_provenance_is_required(db):
    service = EventCandidateService()
    with pytest.raises(ProvenanceRequired):
        service.propose(
            db,
            EventCandidateProposal(
                event_type=EventType.NEWS,
                title="No source",
                source_id=uuid.uuid4(),
            ),
        )


def test_ambiguous_explicit_association_is_refused(db):
    entities.get_or_create_company(db, CompanyIn(legal_name="One", ticker="DUP", cik="0000000021"))
    entities.get_or_create_company(db, CompanyIn(legal_name="Two", ticker="DUP", cik="0000000022", exchange="NYSE"))
    src = source(db, url="https://example.com/dup")
    ev = evidence(db, src.id)
    service = EventCandidateService()
    candidate, _ = service.propose(
        db,
        EventCandidateProposal(event_type=EventType.OTHER, title="Dup", source_id=src.id, evidence_id=ev.id),
    )
    with pytest.raises(AmbiguousEntity):
        service.accept(
            db,
            candidate.id,
            associations=[EventAssociationIn(entity=EntityRef(ticker="DUP"), role=EventEntityRole.SUBJECT)],
        )
