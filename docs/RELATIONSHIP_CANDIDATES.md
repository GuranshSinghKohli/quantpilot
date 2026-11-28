# Relationship candidates

A **candidate** is a proposed edge. It is not a Postgres `relationships` row and is never projected to Neo4j.

```
candidate
   ↓
validation (resolve, type, evidence, provenance)
   ↓
PENDING | AMBIGUOUS
   ↓ accept
ACCEPTED → relationships → Neo4j projection
   ↓ reject
REJECTED (no edge)
```

## Table (`relationship_candidates`, migration `0009`)

| Field | Role |
|---|---|
| `id` | `candidate_id` |
| `from_entity_id` / `to_entity_id` | Resolved endpoints |
| `relationship_type` | Closed vocabulary |
| `source_id` / `evidence_id` | Required |
| `confidence` | 0.0–1.0 strength |
| `provenance_kind` | `OBSERVED` / `VERIFIED` / `INFERRED` |
| `status` | `PENDING` / `ACCEPTED` / `REJECTED` / `AMBIGUOUS` |
| `discovered_at` / `reviewed_at` | Lifecycle |

Identity (unique, idempotent): `(from_entity_id, relationship_type, to_entity_id, evidence_id)`.

Different evidence for the same triple produces separate candidates. Future agents can attach multiple excerpts without collapsing them.

## Evidence

Required: existing `source_id` and `evidence_id` that belong together.

**Not sufficient:** “both companies appear in the article.”

**May support a relationship:** the excerpt explicitly states partnership, acquisition, ownership, supply, development, etc. (closed cue list), or `extraction_method` is `fixture` / `structured`.

Co-mention is not a candidate and not an edge.

## Provenance vs confidence

`confidence=0.95` is not `VERIFIED`. Provenance is evidence status. Confidence is strength. `INFERRED` never overwrites `VERIFIED`.

## Acceptance

`RelationshipCandidateService.accept` calls the existing `persist_relationship` path. Repeated accept is idempotent. A rejected candidate cannot be accepted and never appears in Neo4j.

## Structured sources (this phase)

Deterministic only. No web scrape, no LLM, no co-occurrence inference.

In-repo sources today:

- evidenced fixtures (`python -m app.graph seed-fixtures`)
- explicit `CandidateProposal` writes in services/tests

SEC `company_tickers_exchange.json` is identity/enrichment data, not a relationship feed. Acquisition/ownership records are not ingested yet.

## Queries

Bounded, parameterized, read-only:

- `GET /api/entities/{id}/candidates`
- `GET /api/entities/{id}/aliases`
- `GET /api/entities/{id}/provenance`
- `GET /api/relationships/{id}/provenance`
- neighbors include relationship type, provenance, and confidence

No client Cypher.
