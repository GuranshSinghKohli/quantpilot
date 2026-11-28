"""Shared fixtures for research unit tests. SYNTHETIC data only."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.domain.enums import EventEntityRole, EventStatus, EventType, RelationshipType
from app.domain.schemas import EventEntityIn, EventIn, EvidenceIn
from app.graph.resolve import EntityRef
from app.graph.service import RelationshipCandidate, persist_relationship
from app.repositories import entities, events, sources
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.store import embed_documents
from tests.unit.retrieval.helpers import NOW, close_db, company, migrated_db, news_source


def db_session(tmp_path: Path) -> Session:
    return migrated_db(tmp_path)


def seed_partnership(db: Session):
    amd = company(db, "Advanced Micro Devices Inc", "AMD", "2488")
    meta = company(db, "Meta Platforms Inc", "META", "1326801")
    src = news_source(
        db,
        "https://example.com/research/amd-meta",
        "AMD and Meta announce AI infrastructure partnership",
        "AMD and Meta announced a partnership to deploy Instinct accelerators.",
        ["AMD", "META"],
    )
    ev, _ = sources.add_evidence(
        db,
        src.id,
        EvidenceIn(
            excerpt="AMD and Meta announced a partnership to deploy Instinct accelerators.",
            extraction_method="test",
            extracted_at=NOW,
        ),
    )
    persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=amd.entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=meta.entity_id),
            source_id=src.id,
            evidence_id=ev.id,
            confidence=1.0,
            observed_at=NOW,
        ),
    )
    event, _ = events.upsert_event(
        db,
        EventIn(
            event_type=EventType.PARTNERSHIP,
            title="AMD Meta partnership",
            detected_at=NOW,
            occurred_at=NOW,
            published_at=NOW,
            primary_source_id=src.id,
            evidence_id=ev.id,
            entities=[
                EventEntityIn(
                    entity_id=amd.entity_id,
                    role=EventEntityRole.SUBJECT,
                    source_id=src.id,
                    evidence_id=ev.id,
                )
            ],
        ),
    )
    event.status = EventStatus.CONFIRMED.value
    news_source(
        db,
        "https://example.com/research/conference",
        "Apple and Microsoft appear at the same industry conference",
        "Apple and Microsoft executives spoke on separate panels. No deal was announced.",
        ["AAPL", "MSFT"],
    )
    company(db, "Apple Inc", "AAPL", "320193")
    company(db, "Microsoft Corporation", "MSFT", "789019")
    index_documents(db)
    embed_documents(db, provider=FakeEmbeddingProvider())
    return {"amd": amd, "meta": meta, "event": event, "evidence": ev, "source": src}


def package_with_market(citation_id: str = "mkt1") -> dict:
    return {
        "request": {"query": "AMD window", "entity_ids": [], "event_ids": [], "date_from": None, "date_to": None, "document_types": []},
        "entities": [],
        "events": [],
        "graph_facts": [],
        "documents": [],
        "market_facts": [
            {
                "event_id": "evt-1",
                "symbol": "AMD",
                "windows": {"+5D": {"return": 0.032, "benchmark_return": 0.01, "relative_return": 0.022}},
                "calculation_version": "v1",
                "source": {"record": "event_impacts", "event_id": "evt-1", "calculation_version": "v1"},
            }
        ],
        "citations": [
            {
                "citation_id": citation_id,
                "kind": "market_fact",
                "source_id": None,
                "document_id": None,
                "chunk_id": None,
                "evidence_id": None,
                "source_url": None,
                "title": "AMD v1",
                "published_at": None,
                "locator": {"event_id": "evt-1", "calculation_version": "v1"},
            }
        ],
        "retrieval_metadata": {"semantic_available": True, "semantic_reason": None, "citation_coverage": 1.0, "limits": {}},
    }
