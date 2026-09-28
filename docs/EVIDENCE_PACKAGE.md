# EvidencePackage (Phase 4.1)

`ContextAssembler` is a deterministic service. It is not LangGraph and does not write claims.

Phase 4.2 agents consume this package via `POST /api/research`. They still must not search tables directly. See `docs/PHASE4_2_PLAN.md`.

Default ceilings: 20 entities, 20 events, 20 relationships, 10 documents/chunks, 500-character excerpts. Requests above the ceiling are rejected.

Sections: `request`, `entities`, `events`, `market_facts`, `graph_facts`, `documents`, `citations`, `retrieval_metadata`.

Provenance rules:

- Structured entity facts cite the `companies` / `entities` record.
- Events require `source_id` or `evidence_id`.
- Graph facts require `source_id` + `evidence_id` (edges without evidence are omitted).
- Market facts cite `event_impacts.calculation_version`.
- Document hits require `source_id` + `document_id` + `chunk_id` and use the stored URL (never invented).

`citation_coverage` is kept / eligible factual items. Semantic unavailability is explicit in `retrieval_metadata`.
