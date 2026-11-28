# QuantPilot — Phase 3.2 Plan: Graph Reliability, Entity Enrichment & Relationship Candidates

Status: **as-built with this step.**
Baseline: `94cf7a1 feat: establish entity relationship graph foundation` (Alembic head `0008`).

Phase 3.2 makes the graph trustworthy enough to become the foundation for later market/event intelligence. It does **not** add LangGraph, RAG, pgvector, LLM extraction, market-impact analysis, or a frontend graph.

```
Canonical sources
      ↓
Entity resolution
      ↓
Entity enrichment
      ↓
Relationship candidates
      ↓
Evidence validation
      ↓
PostgreSQL relationship
      ↓
Neo4j projection
      ↓
Graph reconciliation
```

Central rule (unchanged):

- **PostgreSQL** = source of truth
- **Neo4j** = derived query/index layer

---

## 1. Graph reliability goals

- Neo4j tests never share the developer `graph` instance.
- Repeated Neo4j tests leave **zero** leftover test entities on the isolated instance.
- Projection remains additive and retryable. A Neo4j failure never rolls back PostgreSQL.
- Reconciliation compares the two stores by **stable IDs** (`entity_id`, `relationship_id`), never names.
- Repair is opt-in and non-destructive by default.
- Entity resolution stays conservative: unresolved and ambiguous are first-class results.
- A relationship **candidate** is not a graph edge. Only an accepted candidate may become a PostgreSQL relationship and then a Neo4j edge.

---

## 2. Test isolation strategy

Neo4j Community 5 has one database (`neo4j`). Isolation is therefore a **dedicated container**, not a second logical database.

| Instance | Compose profile | Host bolt | Volume | Password |
|---|---|---|---|---|
| Developer graph | `graph` | `localhost:7687` | `quantpilot_neo4j` | `quantpilot-dev-only` |
| Disposable tests | `graph-test` | `localhost:17687` | `quantpilot_neo4j_test` | `quantpilot-test-only` |

Rules:

- Tests require `TEST_NEO4J_URI`. They do **not** fall back to `NEO4J_URI`.
- `app.graph.isolation.require_test_neo4j_uri` rejects developer URIs (`localhost:7687`, `127.0.0.1:7687`, hostname `neo4j`).
- Allowed test URIs: `bolt://localhost:17687`, `bolt://127.0.0.1:17687`, `bolt://neo4j-test:7687`.
- Session start wipes the **test** graph only, after the URI guard passes.
- Each test wipes again on teardown. A second run starts empty.
- The guard never deletes data on the developer instance. If the URI is rejected, tests fail closed.

Kafka:

| Where | `TEST_KAFKA_BOOTSTRAP` |
|---|---|
| Host (`make test-kafka`) | `localhost:9092` |
| `spark-test` container | `kafka:29092` (compose default) |

---

## 3. Reconciliation model

`python -m app.graph reconcile` is **read-only**.

It loads PostgreSQL entities/relationships and Neo4j `:Entity` nodes / typed edges, then diffs by ID:

| Finding | Meaning |
|---|---|
| `missing_entities` | Postgres `entity_id` absent in Neo4j |
| `extra_entities` | Neo4j `entity_id` absent in Postgres |
| `missing_relationships` | Postgres `relationship_id` absent in Neo4j |
| `extra_relationships` | Neo4j `relationship_id` absent in Postgres |
| `property_mismatches` | Same ID, different projected properties |
| `projection_failures` | Rows in `graph_projections` with `status=failed` |

Extra Neo4j data is **reported, never deleted**.

---

## 4. Enrichment model

SEC `company_tickers_exchange.json` fields: `cik`, `name`, `ticker`, `exchange`.

Populated on the company entity / Neo4j node when present:

- `cik`, `legal_name`, `tickers`, `exchange`, `source_system`, `source_key`

**Not populated** (source does not provide them): `sic`, `country`, `industry`.

Aliases (idempotent, tickers never become identity):

- legal name and its normalized form
- display / canonical name
- a deterministic **legal-stem** (`NVIDIA CORPORATION` → `NVIDIA`) by stripping a closed suffix list
- every listed ticker
- existing source names as `SOURCE_ALIAS` when they differ

Ticker reuse: two entities with the same ticker alias → `AMBIGUOUS`. A ticker is not a permanent company key.

---

## 5. Candidate relationship model

New table `relationship_candidates` (migration `0009`). A candidate is **not** a `relationships` row and is **never** projected to Neo4j.

Statuses: `PENDING` | `ACCEPTED` | `REJECTED` | `AMBIGUOUS`.

Identity (unique): `(from_entity_id, relationship_type, to_entity_id, evidence_id)`.

Acceptance calls the existing `persist_relationship` path. Rejection leaves PostgreSQL relationships and Neo4j unchanged.

---

## 6. Evidence requirements

Every candidate needs `source_id` and `evidence_id` pointing at existing rows that match each other.

Insufficient: “both companies appear in the article.”

Sufficient (deterministic, no LLM):

- excerpt explicitly states a partnership / acquisition / supply / ownership / development relationship (closed cue list per type), or
- `extraction_method` is a documented structured exception: `fixture` or `structured`

Co-mention fixtures still do not create candidates or edges.

---

## 7. Provenance vs confidence

| Field | Meaning |
|---|---|
| `confidence` | 0.0–1.0 strength of the assertion |
| `provenance_kind` | `OBSERVED` / `VERIFIED` / `INFERRED` — evidence status |

`confidence=0.95` is **not** `VERIFIED`. `INFERRED` is never treated as fact.

---

## 8. Synchronization behavior

1. Canonical write lands in PostgreSQL.
2. `python -m app.graph project` MERGEs into Neo4j and records per-row `graph_projections` plus a `graph_projection_runs` checkpoint.
3. `reconcile` compares the two stores.
4. `repair` (opt-in) upserts missing nodes/edges and refreshes mismatched properties.
5. Deletion of extra Neo4j data requires `--allow-delete --confirm-delete`.

Default projection stays additive.

---

## 9. Failure / recovery

| Failure | Effect |
|---|---|
| Neo4j down during project/repair | Postgres unchanged; CLI exits 2 |
| Neo4j down during reconcile/quality | Report `neo4j=unavailable`; Postgres quality still runs |
| Candidate reject | No relationship row; no Neo4j edge |
| Ambiguous resolution | No silent pick; status `AMBIGUOUS` |

Recovery: start the isolated or developer Neo4j as appropriate, then `project` or `repair`. Do not wipe the developer volume from tests.

---

## 10. Operational commands

```
make graph-up              # developer Neo4j (profile graph)
make graph-test-up         # disposable Neo4j (profile graph-test)
make graph-status
make graph-project
make graph-reconcile       # read-only
make graph-repair          # opt-in upserts
make graph-quality         # read-only
make graph-check
make graph-seed
make test-neo4j            # isolated URI only
make test-kafka            # host → localhost:9092
make test-spark            # container → kafka:29092
```

---

## 11. Testing strategy

- Host: `pytest tests -q` with the integration Postgres + Kafka + MinIO already used by Phase 3.1.
- Spark image: `docker compose --profile spark run spark-test pytest tests` — no manual `TEST_KAFKA_BOOTSTRAP`.
- Neo4j: `make graph-test-up && make test-neo4j` against port `17687` only.
- Isolation tests prove empty → write → cleanup → empty, and refuse `localhost:7687`.
- Dev SQLite (`backend/data/quantpilot.db`) stays pinned away by `tests/conftest.py`.

---

## 12. Limitations

- No fuzzy entity match. No LLM extraction. No article co-occurrence edges.
- No SIC / country / industry (not in the SEC ticker file).
- No automatic Neo4j delete in `reconcile` or default `project`.
- No Kafka relationship events. No frontend graph. No pgvector / RAG.
- Ticker history is not a bi-temporal series; reuse is detected as `AMBIGUOUS`.
- Structured relationship sources today are evidenced fixtures and explicit service calls, not scraped filings.
