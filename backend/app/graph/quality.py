"""Read-only graph quality report. Postgres first; Neo4j counts when available."""

from __future__ import annotations

import time
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import CandidateStatus, ProvenanceKind
from app.domain.models import (
    Entity,
    EntityAlias,
    GraphProjection,
    Relationship,
    RelationshipCandidateRecord,
    RelationshipEvidence,
)
from app.graph.metrics import GRAPH_METRICS
from app.graph.reconcile import reconcile, reconcile_summary
from app.graph.store import probe_neo4j
from app.observability.logger import get_logger, log_event

logger = get_logger("graph")


def graph_quality(db: Session) -> dict:
    started = time.perf_counter()
    entities = list(db.scalars(select(Entity)))
    missing_norm = [str(e.id) for e in entities if not (e.normalized_name or "").strip()]
    missing_source = [str(e.id) for e in entities if not e.source_system or not e.source_key]

    aliases = list(db.scalars(select(EntityAlias)))
    alias_key_counts: dict[tuple[str, str], int] = defaultdict(int)
    entity_ids = {e.id for e in entities}
    unresolved_alias_refs = []
    for alias in aliases:
        alias_key_counts[(alias.alias_type, alias.normalized_alias)] += 1
        if alias.entity_id not in entity_ids:
            unresolved_alias_refs.append(str(alias.id))
    duplicate_aliases = [
        {"alias_type": kind, "normalized_alias": norm, "count": count}
        for (kind, norm), count in alias_key_counts.items()
        if count > 1 and kind == "TICKER"
    ]

    edges = list(db.scalars(select(Relationship)))
    evidence_ids = {
        row[0]
        for row in db.execute(select(RelationshipEvidence.relationship_id)).all()
    }
    missing_evidence = [str(e.id) for e in edges if e.id not in evidence_ids]
    missing_source_rel = []
    for edge in edges:
        if edge.id not in evidence_ids:
            missing_source_rel.append(str(edge.id))
    invalid_confidence = [
        str(e.id)
        for e in edges
        if e.confidence is not None and (e.confidence < 0 or e.confidence > 1)
    ]
    valid_prov = {k.value for k in ProvenanceKind}
    invalid_provenance = [str(e.id) for e in edges if e.provenance_kind not in valid_prov]
    self_rels = [str(e.id) for e in edges if e.subject_entity_id == e.object_entity_id]
    triples: dict[tuple, list[str]] = defaultdict(list)
    for edge in edges:
        triples[(edge.subject_entity_id, edge.relationship_type, edge.object_entity_id)].append(str(edge.id))
    duplicate_relationships = [ids for ids in triples.values() if len(ids) > 1]

    candidates = list(db.scalars(select(RelationshipCandidateRecord)))
    by_status = {status.value: 0 for status in CandidateStatus}
    for row in candidates:
        by_status[row.status] = by_status.get(row.status, 0) + 1

    projection = {
        "neo4j": probe_neo4j(),
        "failed_rows": db.scalar(
            select(func.count()).select_from(GraphProjection).where(GraphProjection.status == "failed")
        )
        or 0,
    }
    if projection["neo4j"] == "ok":
        summary = reconcile_summary(reconcile(db))
        projection.update(
            {
                "entities_postgres": summary["entities_postgres"],
                "entities_neo4j": summary["entities_neo4j"],
                "missing_nodes": summary["missing_entities"],
                "extra_nodes": summary["extra_entities"],
                "relationships_postgres": summary["relationships_postgres"],
                "relationships_neo4j": summary["relationships_neo4j"],
                "missing_edges": summary["missing_relationships"],
                "extra_edges": summary["extra_relationships"],
            }
        )

    duration = time.perf_counter() - started
    log_event(
        logger,
        20,
        "GRAPH_QUALITY",
        component="graph",
        operation="quality",
        status="ok",
        duration_ms=int(duration * 1000),
    )
    GRAPH_METRICS.inc("graph_reconciliation_total", operation="quality", status="ok")
    return {
        "entities": {
            "total": len(entities),
            "missing_normalized_names": len(missing_norm),
            "missing_source_identity": len(missing_source),
            "duplicate_ticker_aliases": len(duplicate_aliases),
            "unresolved_alias_references": len(unresolved_alias_refs),
        },
        "relationships": {
            "total": len(edges),
            "missing_evidence": len(missing_evidence),
            "missing_source": len(missing_source_rel),
            "invalid_confidence": len(invalid_confidence),
            "invalid_provenance": len(invalid_provenance),
            "self_relationships": len(self_rels),
            "duplicate_relationships": len(duplicate_relationships),
        },
        "projection": projection,
        "candidates": {
            "pending": by_status[CandidateStatus.PENDING.value],
            "accepted": by_status[CandidateStatus.ACCEPTED.value],
            "rejected": by_status[CandidateStatus.REJECTED.value],
            "ambiguous": by_status[CandidateStatus.AMBIGUOUS.value],
        },
        "duration_seconds": round(duration, 3),
    }
