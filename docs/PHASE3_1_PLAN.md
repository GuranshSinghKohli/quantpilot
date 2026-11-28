# QuantPilot — Phase 3.1 Plan: Entity & Relationship Intelligence Foundation

Status: **as-built with this step.**
Baseline: `ffb70c6 feat: harden data platform observability and recovery` (Alembic head `0007`).

Phase 3.1 builds the deterministic entity/relationship foundation and a derived Neo4j projection. It does **not** add LangGraph agents, RAG, pgvector, LLM extraction, research reports, or a frontend graph.

```
External sources
      ↓
canonical Postgres (entities, companies, technologies, sources, evidence, relationships)
      ↓
entity resolution + relationship service (Postgres validation)
      ↓
graph projector (idempotent upserts)
      ↓
Neo4j (derived intelligence graph; optional)
```

---

## 1. Graph goals

- PostgreSQL remains the system of record for entities, aliases, relationships, and provenance (ADR-002, ADR-006, ADR-007).
- Neo4j is a **projection**: rebuilt or incrementally upserted from Postgres. It is not required for the API to start.
- Every relationship is directed, typed from a closed vocabulary, and traceable to a `sources` + `evidence_records` pair.
- Identity is stable (`entities.id` / CIK for SEC companies). Names and tickers are aliases.
- Resolution is conservative: exact CIK, exact source key, exact normalized name, exact ticker. Never fuzzy merge. Ambiguity is returned, not guessed.
- Co-mention is not a relationship. Two names in one document do not create an edge.

## 2. Entity model

**Reuse, do not duplicate.** `entities` is already the graph-node supertable (ADR-006). This step does **not** create a second `graph_entities` table.

| Postgres | Meaning |
|---|---|
| `entities.id` | Stable `entity_id` |
| `entities.entity_type` | `company`, `technology`, `organization`, `person`, **`product`**, **`event`** |
| `entities.canonical_name` | Display / legal name (unique per type; existing rule) |
| `entities.normalized_name` | Exact-match key (new) |
| `entities.source_system` + `entities.source_key` | External identity (new). SEC: `sec` + CIK |
| `companies` / `technologies` | Typed extensions; company identity remains CIK |

Neo4j labels: `:Entity` plus `:Company` / `:Technology` / `:Product` / `:Person` / `:Organization` / `:Event`.

## 3. Relationship model

**Reuse `relationships` + `relationship_evidence`.** No second `graph_relationships` table.

Directed triple: `(subject_entity_id, relationship_type, object_entity_id)` — unique. Inverse is never implied.

Vocabulary (existing + `CUSTOMER_OF`):

`INVESTED_IN`, `PARTNERED_WITH`, `SUPPLIES`, `COMPETES_WITH`, `USES`, `ACQUIRED`, `DEVELOPS`, `OWNS`, `CUSTOMERS_OF`, `CUSTOMER_OF`, `DEPENDS_ON`.

`CUSTOMERS_OF` is kept (migration `0003`). `CUSTOMER_OF` is the spec name; both are stored as written.

New column: `provenance_kind` ∈ `OBSERVED` | `VERIFIED` | `INFERRED`.

| Kind | Meaning | Treated as fact? |
|---|---|---|
| `OBSERVED` | Source excerpt states the edge; not independently confirmed | No — reported as observed |
| `VERIFIED` | Explicitly marked verified (supporting evidence required) | Yes, still with evidence ids |
| `INFERRED` | Derived / hypothesized | **Never** silently treated as fact |

An edge without `relationship_evidence` may exist from the Phase 1 repository (`asserted`). It is **not** a verified relationship. The Phase 3.1 relationship service refuses new writes without `source_id` + `evidence_id`.

## 4. Identity strategy

1. **Primary (companies):** `companies.cik` (unique) → `entities.id`. `source_system='sec'`, `source_key=CIK`.
2. **Primary (any typed external id):** unique `(source_system, source_key)` when both are set.
3. **Aliases** (`entity_aliases`): `LEGAL_NAME`, `DISPLAY_NAME`, `TICKER`, `FORMER_NAME`, `PRODUCT_ALIAS`, `TECHNOLOGY_ALIAS`, `SOURCE_ALIAS`. Unique per `(entity_id, alias_type, normalized_alias)`. Tickers are **not** globally unique — two hits → `AMBIGUOUS`.
4. **Never** use company name as the primary key. `uq_entities_type_name` remains a display uniqueness guard, not SEC identity.

## 5. Provenance strategy

Minimum for a **new** graph-service write:

`source_id`, `evidence_id` (existing `evidence_records` row), and through that row: `source.url`, `source.published_at`, `evidence.extracted_at`.

The evidence excerpt must be stored; we do not invent a relationship because two entities appear in the same document. Callers pass an explicit `relationship_type`.

## 6. Neo4j schema

See `docs/GRAPH_SCHEMA.md`. Constraints: unique `Entity.entity_id`; indexes on `canonical_name`, `entity_type`, `Company.cik`. Relationship types match Postgres. Properties: `relationship_id`, `confidence`, `source_id`, `evidence_id`, `valid_from`, `valid_to`, `provenance_kind`.

## 7. Synchronization

```
Postgres entities / aliases / relationships
        ↓
app.graph.projector  (MERGE on entity_id / relationship_id)
        ↓
Neo4j
```

- Idempotent. Second run upserts, does not duplicate.
- Additive in this phase (no full-graph delete).
- `graph_projections` records last attempt per `(record_kind, record_id)`.
- No distributed transaction. Postgres commit first; Neo4j failure is retryable.

## 8. Idempotency

- Entity: CIK / `(source_system, source_key)` / existing `entities.id`.
- Alias: `(entity_id, alias_type, normalized_alias)`.
- Relationship: existing unique triple. Same evidence → no-op. New supporting evidence → link. Weaker/older confidence does not overwrite a stronger/newer verified edge.
- Projection: MERGE on stable ids.

## 9. Deletion / update semantics

- No automatic Neo4j deletes in this step.
- Retracted Postgres edges stay retracted; projection copies `status`.
- Company name/ticker updates refresh aliases; CIK does not change.

## 10. Failure / recovery

- Neo4j down: Postgres writes succeed; `/ready` reports `neo4j=unavailable` and stays `ok`; API depth-1 reads use Postgres.
- `python -m app.graph project` is safe to rerun.
- `python -m app.graph check` compares counts and pending projections; it does not mutate stuck state.

## 11. Testing strategy

- Unit: normalize, resolve, ambiguity, validation, provenance, co-mention refusal, cardinality of metric labels.
- Postgres integration: constraints, replay, conflict, concurrent insert.
- Neo4j tier (`tests/neo4j/`, `TEST_NEO4J_URI`): schema, upsert, repeat project, bounded queries. Skip if unset.
- Existing host / Spark / Kafka / S3 / Postgres suites must stay green. Tests never open the dev SQLite.

## 12. Known limitations

- No LLM extraction; relationships enter via the service or deterministic fixtures.
- No Kafka `relationship.upserted` topic yet (ADR-010 future).
- No Grafana / frontend graph.
- Depth > 1 neighbor queries need Neo4j; depth 1 is served from Postgres.
- `CUSTOMERS_OF` (0003) and `CUSTOMER_OF` (0008) both exist.
- Name uniqueness `uq_entities_type_name` can still force the loader's `Name (CIK …)` suffix; identity remains CIK.
