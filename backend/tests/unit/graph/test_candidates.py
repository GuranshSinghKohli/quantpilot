"""Relationship candidates are not graph edges until accepted."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import CandidateStatus, ProvenanceKind, RelationshipType, SourceType
from app.domain.models import Relationship, RelationshipCandidateRecord
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn
from app.graph.aliases import upsert_alias
from app.graph.candidates import CandidateProposal, RelationshipCandidateService
from app.graph.errors import AmbiguousEntity, EvidenceInsufficient, ProvenanceRequired, UnresolvedEntity
from app.graph.resolve import EntityRef
from app.domain.enums import AliasType
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
svc = RelationshipCandidateService()


@pytest.fixture()
def db(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'candidates.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def _companies(db):
    nvda, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810")
    )
    tsm, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="Taiwan Semiconductor", ticker="TSM", cik="1046177")
    )
    return nvda, tsm


def _evidence(db, excerpt: str, url: str, method: str = "test"):
    source, _ = sources.upsert_source(
        db, SourceIn(source_type=SourceType.WEB, url=url, retrieved_at=NOW, content_text=excerpt, title="t")
    )
    ev, _ = sources.add_evidence(
        db, source.id, EvidenceIn(excerpt=excerpt, extraction_method=method, extracted_at=NOW)
    )
    return source, ev


def test_create_and_duplicate_candidate(db):
    nvda, tsm = _companies(db)
    source, ev = _evidence(db, "TSMC supplies NVIDIA with advanced packaging.", "https://example.com/cand-1")
    first, created = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.8,
        ),
    )
    again, created_again = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.8,
        ),
    )
    assert created and not created_again and first.id == again.id
    assert first.status == CandidateStatus.PENDING.value
    assert db.query(Relationship).count() == 0


def test_candidate_without_evidence_or_source_rejected(db):
    nvda, tsm = _companies(db)
    with pytest.raises(ProvenanceRequired):
        svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(entity_id=tsm.entity_id),
                relationship_type=RelationshipType.SUPPLIES,
                to_entity=EntityRef(entity_id=nvda.entity_id),
                source_id=uuid.uuid4(),
                evidence_id=uuid.uuid4(),
                confidence=0.5,
            ),
        )


def test_co_mention_is_not_enough_for_a_candidate(db):
    nvda, tsm = _companies(db)
    source, ev = _evidence(
        db,
        "The article mentions TSMC and NVIDIA in the same paragraph.",
        "https://example.com/co-mention-cand",
    )
    with pytest.raises(EvidenceInsufficient):
        svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(entity_id=tsm.entity_id),
                relationship_type=RelationshipType.PARTNERED_WITH,
                to_entity=EntityRef(entity_id=nvda.entity_id),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.4,
            ),
        )
    assert db.query(RelationshipCandidateRecord).count() == 0


def test_accept_creates_canonical_relationship_idempotently(db):
    nvda, tsm = _companies(db)
    source, ev = _evidence(db, "TSMC supplies NVIDIA with advanced packaging.", "https://example.com/accept")
    row, _ = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(ticker="TSM"),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(cik="1045810"),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.9,
            provenance_kind=ProvenanceKind.OBSERVED,
        ),
    )
    accepted, edge = svc.accept(db, row.id)
    again, edge_again = svc.accept(db, row.id)
    assert accepted.status == CandidateStatus.ACCEPTED.value
    assert accepted.accepted_relationship_id == edge.id == edge_again.id
    assert again.id == accepted.id
    assert db.query(Relationship).count() == 1


def test_reject_never_creates_a_relationship(db):
    nvda, tsm = _companies(db)
    source, ev = _evidence(db, "TSMC supplies NVIDIA with advanced packaging.", "https://example.com/reject")
    row, _ = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.5,
        ),
    )
    rejected = svc.reject(db, row.id)
    assert rejected.status == CandidateStatus.REJECTED.value
    assert db.query(Relationship).count() == 0
    with pytest.raises(ProvenanceRequired):
        svc.accept(db, row.id)


def test_ambiguous_endpoints_do_not_create_a_candidate(db):
    a, _ = entities.get_or_create_company(db, CompanyIn(legal_name="One Corp", ticker="ONE", cik="1"))
    b, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Two Corp", ticker="TWO", cik="2"))
    upsert_alias(db, b.entity_id, "ONE", AliasType.TICKER)
    source, ev = _evidence(db, "One supplies Two.", "https://example.com/ambig-cand")
    with pytest.raises(AmbiguousEntity):
        svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(ticker="ONE", entity_type="company"),
                relationship_type=RelationshipType.SUPPLIES,
                to_entity=EntityRef(entity_id=b.entity_id),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.5,
            ),
        )
    assert db.query(RelationshipCandidateRecord).count() == 0


def test_unresolved_endpoints_rejected(db):
    source, ev = _evidence(db, "Nobody supplies Nobody.", "https://example.com/unresolved-cand")
    with pytest.raises(UnresolvedEntity):
        svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(name="Missing Corp"),
                relationship_type=RelationshipType.SUPPLIES,
                to_entity=EntityRef(name="Also Missing"),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.5,
            ),
        )


def test_weaker_evidence_does_not_overwrite_verified(db):
    nvda, tsm = _companies(db)
    strong, ev_strong = _evidence(db, "TSMC supplies NVIDIA.", "https://example.com/strong", method="fixture")
    weak, ev_weak = _evidence(db, "An analyst infers TSMC supplies NVIDIA.", "https://example.com/weak")
    first, _ = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=strong.id,
            evidence_id=ev_strong.id,
            confidence=0.7,
            provenance_kind=ProvenanceKind.VERIFIED,
        ),
    )
    _, edge = svc.accept(db, first.id)
    second, _ = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=weak.id,
            evidence_id=ev_weak.id,
            confidence=0.99,
            provenance_kind=ProvenanceKind.INFERRED,
        ),
    )
    _, again = svc.accept(db, second.id)
    assert again.id == edge.id
    assert again.provenance_kind == ProvenanceKind.VERIFIED.value
    assert again.confidence == 0.7


def test_different_evidence_is_a_separate_candidate(db):
    nvda, tsm = _companies(db)
    s1, e1 = _evidence(db, "TSMC supplies NVIDIA packaging.", "https://example.com/e1")
    s2, e2 = _evidence(db, "TSMC supplies NVIDIA wafers.", "https://example.com/e2")
    a, _ = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=s1.id,
            evidence_id=e1.id,
            confidence=0.6,
        ),
    )
    b, created = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=s2.id,
            evidence_id=e2.id,
            confidence=0.6,
        ),
    )
    assert created and a.id != b.id
