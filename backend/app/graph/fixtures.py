"""Deterministic, evidenced relationship fixtures. Never infer from co-mention."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from app.domain.enums import ProvenanceKind, RelationshipType, SourceType
from app.domain.schemas import EvidenceIn, SourceIn, TechnologyIn
from app.graph.resolve import EntityRef
from app.graph.service import RelationshipCandidate, persist_relationship
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

# Excerpt text must *state* the relationship. Mentioning two names is not enough.
FIXTURES = (
    {
        "from": EntityRef(ticker="NVDA", entity_type="company"),
        "type": RelationshipType.DEVELOPS,
        "to_tech": TechnologyIn(name="CUDA", slug="cuda", category="runtime", description="NVIDIA parallel compute platform"),
        "url": "https://developer.nvidia.com/cuda-zone",
        "title": "CUDA Zone",
        "excerpt": "NVIDIA develops CUDA, a parallel computing platform and programming model.",
        "kind": ProvenanceKind.OBSERVED,
    },
    {
        "from": EntityRef(ticker="TSM", entity_type="company"),
        "type": RelationshipType.SUPPLIES,
        "to": EntityRef(ticker="NVDA", entity_type="company"),
        "url": "https://www.example.com/fixture/tsmc-supplies-nvidia",
        "title": "Fixture: TSMC supplies NVIDIA",
        "excerpt": "TSMC supplies advanced packaging capacity used by NVIDIA for accelerator production.",
        "kind": ProvenanceKind.OBSERVED,
    },
)


def seed_fixtures(db: Session) -> dict:
    created: List[str] = []
    skipped: List[str] = []
    for spec in FIXTURES:
        to_ref = spec.get("to")
        if spec.get("to_tech"):
            tech, _ = entities.get_or_create_technology(db, spec["to_tech"])
            to_ref = EntityRef(entity_id=tech.entity_id)
        source, _ = sources.upsert_source(
            db,
            SourceIn(
                source_type=SourceType.WEB,
                url=spec["url"],
                retrieved_at=NOW,
                content_text=spec["excerpt"],
                title=spec["title"],
                publisher="fixture",
                published_at=NOW,
            ),
        )
        evidence, _ = sources.add_evidence(
            db,
            source.id,
            EvidenceIn(excerpt=spec["excerpt"], extraction_method="fixture", extracted_at=NOW),
        )
        try:
            edge, made = persist_relationship(
                db,
                RelationshipCandidate(
                    from_entity=spec["from"],
                    relationship_type=spec["type"],
                    to_entity=to_ref,
                    source_id=source.id,
                    evidence_id=evidence.id,
                    confidence=0.9,
                    provenance_kind=spec["kind"],
                    observed_at=NOW,
                ),
            )
            created.append(str(edge.id) if made else f"duplicate:{edge.id}")
        except Exception as exc:
            skipped.append(f"{spec['type'].value}:{type(exc).__name__}")
    db.commit()
    return {"created": created, "skipped": skipped, "co_mention_not_seeded": True}
