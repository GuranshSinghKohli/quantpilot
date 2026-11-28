"""Normalization, resolution, and relationship-service rules (SQLite)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import (
    EntityType,
    ProvenanceKind,
    RelationshipType,
    ResolutionStatus,
    SourceType,
)
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn, TechnologyIn
from app.graph.aliases import upsert_alias
from app.graph.errors import AmbiguousEntity, ProvenanceRequired, UnresolvedEntity
from app.graph.normalize import normalize_cik, normalize_name, normalize_ticker
from app.graph.resolve import EntityRef, resolve_entity
from app.graph.service import RelationshipCandidate, persist_relationship
from app.domain.enums import AliasType
from app.pipeline.metrics import FORBIDDEN_LABELS, MetricLabelError, Metrics
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_name_normalization_is_exact_not_fuzzy():
    assert normalize_name("Meta Platforms, Inc.") == normalize_name("Meta Platforms Inc")
    assert normalize_name("Meta") != normalize_name("Meta Platforms")
    assert normalize_ticker("nvda") == "NVDA"
    assert normalize_cik("1045810") == "0001045810"
    with pytest.raises(ValueError):
        normalize_cik("not-a-cik")


def test_resolve_company_by_cik_then_name_then_ticker(db):
    company, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810")
    )
    by_cik = resolve_entity(db, EntityRef(cik="0001045810"))
    by_name = resolve_entity(db, EntityRef(name="NVIDIA Corporation", entity_type=EntityType.COMPANY))
    by_ticker = resolve_entity(db, EntityRef(ticker="nvda", entity_type=EntityType.COMPANY))
    assert by_cik.status == by_name.status == by_ticker.status == ResolutionStatus.RESOLVED
    assert by_cik.entity_id == by_name.entity_id == by_ticker.entity_id == company.entity_id
    assert resolve_entity(db, EntityRef(name="Nobody Inc")).status == ResolutionStatus.UNRESOLVED


def test_ambiguous_ticker_is_not_guessed(db):
    a, _ = entities.get_or_create_company(db, CompanyIn(legal_name="One Corp", ticker="ONE", cik="1"))
    b, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Two Corp", ticker="TWO", cik="2"))
    upsert_alias(db, b.entity_id, "ONE", AliasType.TICKER)
    result = resolve_entity(db, EntityRef(ticker="ONE", entity_type=EntityType.COMPANY))
    assert result.status == ResolutionStatus.AMBIGUOUS
    with pytest.raises(AmbiguousEntity):
        result.require()
    assert set(result.candidate_ids) == {a.entity_id, b.entity_id}


def _evidence(db, excerpt: str, url: str = "https://example.com/graph-fixture"):
    source, _ = sources.upsert_source(
        db, SourceIn(source_type=SourceType.WEB, url=url, retrieved_at=NOW, content_text=excerpt, title="t")
    )
    ev, _ = sources.add_evidence(db, source.id, EvidenceIn(excerpt=excerpt, extraction_method="test", extracted_at=NOW))
    return source, ev


def test_relationship_requires_provenance_and_is_directed(db):
    nvda, _ = entities.get_or_create_company(db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810"))
    tsm, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Taiwan Semiconductor", ticker="TSM", cik="1046177"))
    with pytest.raises(ProvenanceRequired):
        persist_relationship(
            db,
            RelationshipCandidate(
                from_entity=EntityRef(entity_id=nvda.entity_id),
                relationship_type=RelationshipType.SUPPLIES,
                to_entity=EntityRef(entity_id=tsm.entity_id),
                source_id=uuid.uuid4(),
                evidence_id=uuid.uuid4(),
                confidence=0.5,
            ),
        )
    source, ev = _evidence(db, "TSMC supplies NVIDIA with advanced packaging.")
    edge, created = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(ticker="TSM"),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(cik="1045810"),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.8,
            provenance_kind=ProvenanceKind.OBSERVED,
        ),
    )
    assert created and edge.subject_entity_id == tsm.entity_id and edge.object_entity_id == nvda.entity_id
    again, created_again = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=tsm.entity_id),
            relationship_type=RelationshipType.SUPPLIES,
            to_entity=EntityRef(entity_id=nvda.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.8,
        ),
    )
    assert not created_again and again.id == edge.id
    inverse = resolve_entity(db, EntityRef(entity_id=nvda.entity_id))
    from app.graph.queries import direct_relationship

    assert direct_relationship(db, nvda.entity_id, tsm.entity_id, "SUPPLIES") == []
    assert inverse.entity_id == nvda.entity_id


def test_co_mention_is_not_a_relationship(db):
    entities.get_or_create_company(db, CompanyIn(legal_name="Meta Platforms", ticker="META", cik="1326801"))
    entities.get_or_create_company(db, CompanyIn(legal_name="Advanced Micro Devices", ticker="AMD", cik="2488"))
    _evidence(db, "The article mentions Meta and AMD in the same paragraph.", url="https://example.com/co-mention")
    from app.domain.models import Relationship

    assert db.query(Relationship).count() == 0


def test_inferred_is_not_verified(db):
    a, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Alpha Co", ticker="ALP", cik="10"))
    b, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Beta Co", ticker="BET", cik="11"))
    source, ev = _evidence(db, "An analyst infers that Alpha depends on Beta.", url="https://example.com/inferred")
    edge, _ = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=a.entity_id),
            relationship_type=RelationshipType.DEPENDS_ON,
            to_entity=EntityRef(entity_id=b.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.4,
            provenance_kind=ProvenanceKind.INFERRED,
        ),
    )
    from app.graph.queries import _edge_payload

    payload = _edge_payload(edge)
    assert payload["inferred"] is True
    assert payload["verified"] is False


def test_unresolved_entity_refuses_write(db):
    source, ev = _evidence(db, "Nobody invested in Nobody.")
    with pytest.raises(UnresolvedEntity):
        persist_relationship(
            db,
            RelationshipCandidate(
                from_entity=EntityRef(name="Missing Corp"),
                relationship_type=RelationshipType.INVESTED_IN,
                to_entity=EntityRef(name="Also Missing"),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.5,
            ),
        )


def test_confidence_and_validity_bounds():
    with pytest.raises(ValidationError):
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=uuid.uuid4()),
            relationship_type=RelationshipType.USES,
            to_entity=EntityRef(entity_id=uuid.uuid4()),
            source_id=uuid.uuid4(),
            evidence_id=uuid.uuid4(),
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=uuid.uuid4()),
            relationship_type=RelationshipType.USES,
            to_entity=EntityRef(entity_id=uuid.uuid4()),
            source_id=uuid.uuid4(),
            evidence_id=uuid.uuid4(),
            confidence=0.5,
            valid_from=NOW,
            valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )


def test_graph_metrics_reject_high_cardinality_labels():
    metrics = Metrics()
    metrics.inc("graph_projection_total", operation="project", status="ok", kind="entity")
    for label in ("entity_id", "relationship_id", "candidate_id", "evidence_id", "ticker", "cik", "url"):
        assert label in FORBIDDEN_LABELS
        with pytest.raises(MetricLabelError):
            metrics.inc("graph_projection_total", **{label: "x"})
    assert "name" in FORBIDDEN_LABELS
    with pytest.raises(TypeError):
        metrics.inc("graph_projection_total", name="x")


def test_technology_resolution(db):
    tech, _ = entities.get_or_create_technology(db, TechnologyIn(name="CUDA", slug="cuda"))
    found = resolve_entity(db, EntityRef(name="CUDA", entity_type=EntityType.TECHNOLOGY))
    assert found.entity_id == tech.entity_id
