"""Measured timings on tiny fixtures. Not capacity claims."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import RelationshipType, SourceType
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn
from app.graph.candidates import CandidateProposal, RelationshipCandidateService
from app.graph.quality import graph_quality
from app.graph.resolve import EntityRef, resolve_entity
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'perf.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_measure_resolution_and_candidate_batches(db):
    companies = []
    for i in range(25):
        company, _ = entities.get_or_create_company(
            db, CompanyIn(legal_name=f"Perf Co {i}", ticker=f"P{i:02d}", cik=str(2000 + i))
        )
        companies.append(company)
    started = time.perf_counter()
    for company in companies:
        resolve_entity(db, EntityRef(cik=company.cik))
    resolution_s = time.perf_counter() - started

    svc = RelationshipCandidateService()
    started = time.perf_counter()
    for i in range(24):
        excerpt = f"Perf Co {i} supplies Perf Co {i + 1}."
        source, _ = sources.upsert_source(
            db,
            SourceIn(
                source_type=SourceType.WEB,
                url=f"https://example.com/perf-{i}",
                retrieved_at=NOW,
                content_text=excerpt,
            ),
        )
        ev, _ = sources.add_evidence(
            db, source.id, EvidenceIn(excerpt=excerpt, extraction_method="test", extracted_at=NOW)
        )
        svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(entity_id=companies[i].entity_id),
                relationship_type=RelationshipType.SUPPLIES,
                to_entity=EntityRef(entity_id=companies[i + 1].entity_id),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.5,
            ),
        )
    candidate_s = time.perf_counter() - started
    started = time.perf_counter()
    graph_quality(db)
    quality_s = time.perf_counter() - started
    print(
        f"PERF resolution_batch_25={resolution_s:.4f}s "
        f"candidate_insert_24={candidate_s:.4f}s quality={quality_s:.4f}s"
    )
    assert resolution_s < 5
    assert candidate_s < 10
    assert quality_s < 5
