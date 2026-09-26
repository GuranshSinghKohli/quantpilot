# QuantPilot — Architecture Decisions

Status of every decision: **Proposed** unless marked otherwise. Each one names the problem it solves, what it rejects, and when it becomes real. Technology is introduced only in the phase where a concrete workload needs it (see `IMPLEMENTATION_ROADMAP.md`).

Baseline facts come from `CURRENT_STATE.md`.

---

## ADR-001 — Evolve the existing repo; don't rewrite it

**Decision.** Keep `backend/` (FastAPI), `frontend/`, `mcp_server/`, `evals/`. Add new packages next to existing ones and migrate code into them phase by phase. Working routes keep working until a replacement ships and is tested.

**Why.** 262 passing tests, a working evidence ledger, and a hardened MCP boundary are worth more than a clean slate. A rewrite would delete understood behavior and produce nothing reviewable for weeks.

**Rejected.** Greenfield `services/` monorepo in one pass (violates "don't build everything at once").

## ADR-002 — PostgreSQL is the system of record

**Owns:** all transactional and relational truth — companies, technologies, sources (metadata), events, relationships (canonical copy), market event windows, research jobs, agent runs, claims, evidence, reports, users. Also the lineage pointers from each row to its S3 object.

**Does not own:** raw document bodies and bulk history (S3), graph traversal (Neo4j projection), high-throughput event transport (Kafka).

**Consequences.**
- SQLite is dropped as the runtime database. It stays only for pure-logic unit tests that don't touch Postgres features, and only until those tests move to the Postgres fixture.
- Use Postgres-native types for new tables: `UUID`, `JSONB`, `TIMESTAMPTZ`, `CHECK` constraints, partial unique indexes.
- The Docker image is `pgvector/pgvector:pg16` from Phase 1 so ADR-008 needs no infra change later. The extension is **not** enabled until it's used.

## ADR-003 — Alembic owns schema changes

**Decision.** Alembic migrations in `backend/alembic/`.
- Migration `0001_baseline` reproduces the current schema exactly. Existing databases are adopted (stamped at `0001` by `upgrade_to_head`), not re-created.
- `0002` drops the dead briefing and alert-rule tables, `0003_core_foundation` adds the Phase 1 core tables, and `0004` renames `analysis_runs.recommendation` to `headline`.
- `create_all` and the `_migrate_*_columns()` ALTERs are removed from the startup path.
- `/ready` reports whether the database is at Alembic head.

**Why.** Silent log-and-continue ALTERs are the top blocker (`CURRENT_STATE.md` §9.1). Every later phase adds tables.

**Rule.** Every migration has a working `downgrade`, and CI runs `upgrade head → downgrade base → upgrade head` against Postgres.

## ADR-004 — Identifiers

**Decision.** New core tables use `UUID` primary keys generated in the application (`uuid4`). Existing ledger tables keep integer IDs until they are migrated.

**Why.** The same identity must survive into Kafka message keys, S3 object paths, and Neo4j node IDs, and must be generatable by a worker before it touches the database (needed for idempotent retries).

**Natural keys for dedup** are separate unique columns, not the primary key: `sources.url_hash`, `events.dedup_key`, `research_jobs.idempotency_key`, `companies (cik)`.

## ADR-005 — Global knowledge vs owner-scoped work

**Global, shared across users:** companies, technologies, entities, sources, evidence records, events, relationships, market event windows. A filing is the same filing for everyone.

**Owner-scoped:** research jobs, agent runs, claims produced inside a job, reports, notifications.

**Why.** Today everything is owner-scoped, which duplicates facts and makes a knowledge graph impossible.

## ADR-006 — Entities supertable for relationships

**Decision.** An `entities` table (`id`, `entity_type`, `canonical_name`) with `companies` and `technologies` as 1:1 extensions keyed by `entity_id`. `relationships` has real foreign keys `subject_entity_id → entities.id` and `object_entity_id → entities.id`.

**Why.** Relationships connect companies to companies, companies to technologies, and later investors and people. Polymorphic `(type, id)` pairs can't be enforced by foreign keys. A supertable keeps referential integrity and maps 1:1 to Neo4j nodes.

**Rejected.** Separate `company_relationships` / `company_technology_relationships` tables (combinatorial growth).

## ADR-007 — Neo4j is a projection, not the source of truth

**Decision (future phase).** Postgres `relationships` plus `relationship_evidence` remain canonical. Neo4j is rebuilt or incrementally synced from Postgres (via Kafka `relationship.upserted` events once Kafka exists). Graph queries serve traversal questions ("second-degree suppliers of X exposed to technology Y").

**Why.** Every relationship must trace to evidence and support retraction. Doing that transactionally is simpler in Postgres. A projection can always be rebuilt; a primary graph store can't be rebuilt from anything.

**Introduced when.** Traversals deeper than two hops are needed and can't be served acceptably by recursive SQL.

## ADR-008 — pgvector replaces ChromaDB, later

**Decision (future phase).** Embeddings for evidence chunks live in a Postgres table with a `vector` column. The embedding model and dimension are recorded per row (`embedding_model`) so re-embedding is explicit.

**Why.** Keeps retrieval transactional with the evidence it points to, removes a local-disk store that isn't durable in containers, and gives one backup story.

**Until then** ChromaDB stays and keeps working.

## ADR-009 — S3 data lake layout and responsibility

**Decision (future phase; MinIO locally, S3 on AWS).**

```
s3://quantpilot-lake/
  raw/{source_type}/ingest_date=YYYY-MM-DD/{source_id}.{json|html|pdf}   # immutable, write-once
  processed/{dataset}/dt=YYYY-MM-DD/part-*.parquet                        # cleaned, normalized
  curated/{events|entities|relationships|market_windows}/dt=.../part-*.parquet
```

- **Raw is immutable.** Re-ingestion writes a new object only if `content_hash` changed.
- Postgres `sources.raw_uri` points to the raw object. That pointer is the bottom of the lineage chain.
- Parquet is for analytics and backfills (Spark), not for serving the API.

**Introduced when.** Ingestion workers exist (the first phase that stores raw documents).

## ADR-010 — Kafka event boundaries

**Decision (future phase).** Kafka is introduced only when there are at least two independent producers and consumers, i.e. ingestion workers plus a research worker. Topics are defined by domain facts, not by function calls:

| Topic | Producer | Consumers | Key |
|---|---|---|---|
| `raw.document.ingested` | news / filing / market ingestion workers | processing, entity extraction | `source_id` |
| `event.detected` | event detector | market analytics, research trigger | `event_id` |
| `entity.resolved` / `relationship.upserted` | entity worker | graph projector | `entity_id` / `relationship_id` |
| `research.job.requested` | API | research worker | `job_id` |
| `research.job.progress` | research worker | API (SSE fan-out), observability | `job_id` |
| `*.dlq` | any consumer after retries | operator tooling | original key |

**Rules.**
- Messages carry IDs and minimal metadata. Bodies stay in S3 or Postgres ("claim-check" pattern).
- Every consumer is idempotent (ADR-013). Delivery is at-least-once.
- Schemas are versioned (`schema_version` field; JSON Schema first, Avro/Schema Registry only if needed).

**Before Kafka exists,** the job queue is a Postgres table (`research_jobs`, polled with `SELECT … FOR UPDATE SKIP LOCKED`). This keeps the API contract identical so Kafka can be swapped in behind it.

## ADR-011 — Airflow vs Spark vs workers

| Tool | Owns | Does not own |
|---|---|---|
| **Workers** (plain Python processes) | Low-latency, per-item work: ingest one document, run one research job, compute one event's windows | Scheduling, bulk backfills |
| **Airflow** (future) | Scheduled and dependency-ordered batch: daily ingestion sweeps, nightly market-window recompute, lake compaction, eval runs, graph rebuild | Per-request work; business logic (DAGs call worker/CLI code) |
| **Spark** (future) | Bulk transforms over the lake: raw → processed → curated Parquet, historical backfills, large event-window computation; Structured Streaming only if streaming volume justifies it | Serving queries; anything that fits comfortably in one Python process |

**Honest scope.** Spark is justified by historical backfills (years of prices × thousands of events), not by today's volume. It is introduced with a measured workload, and the reason is documented next to the job.

APScheduler is removed once Airflow (or a worker-side scheduler) owns the sweep.

## ADR-012 — FastAPI boundaries vs LangGraph responsibilities

**FastAPI (API service) does:** authentication, validation, rate limiting, creating jobs (idempotently), reading jobs/reports/graph data, streaming progress. **It does not run LLM pipelines inside a request** once async jobs exist.

**LangGraph (inside the research worker) does:** orchestration of one research job — agent nodes, bounded loops, state, retries per node, checkpointing. It **does not** own persistence of domain facts directly; nodes call repository/service functions so the same writes are testable without LLMs.

**Service layer (`app/services`, `app/repositories`)** owns business rules and data access, shared by API and workers.

**MCP server** stays the only path to external data from agents.

Target layering, enforced by imports:

```
api (routers) ─► services ─► repositories ─► db
workers ─────► services
workflows (LangGraph) ─► services / mcp_client
domain (models, enums) ◄── everyone
```

## ADR-013 — Async research jobs, idempotency, retries

**Contract (future phase, table created in Phase 1):**

- `POST /api/v1/research` with an `Idempotency-Key` header (or a server-derived key) → `202 Accepted` + `{job_id, status_url}`. The same key returns the same job.
- `GET /api/v1/research/{job_id}` → status, `current_stage`, per-stage agent runs, report when done.
- `GET /api/v1/research/{job_id}/events` → SSE progress.

**Job state machine:** `queued → running → (succeeded | partial | failed | cancelled)`. `partial` is a first-class outcome: a report with explicit gaps beats a failed job.

**Idempotency.**
- Jobs: unique `idempotency_key`.
- Sources: unique `url_hash`; re-fetch only records a new version if `content_hash` changed.
- Events: unique `dedup_key` (normalized type + primary entity + time bucket + primary source).
- Agent runs: `(research_job_id, agent_name, attempt)` unique; `input_hash` lets a retry reuse a completed run.
- All writes from consumers use `INSERT … ON CONFLICT`.

**Retries.** Exponential backoff with jitter for tool and LLM calls, bounded attempts per node, then the node records `failed` and the job continues where the graph allows it (degrading to `partial`). Poison messages go to a DLQ after N attempts.

## ADR-014 — Data lineage

Every report sentence must trace back as: **report → claim → claim_evidence → evidence record → source → raw object (S3) → ingestion run**.

- `reports.research_job_id`, `claims.research_job_id` + `claims.agent_run_id`, `claim_evidence (claim_id, evidence_id, stance)`, `evidence_records.source_id` + locator (section / char span / page), `sources.raw_uri` + `content_hash`.
- `agent_runs` records `model`, `prompt_version`, `input_hash`, token counts, latency, error.
- A claim with zero supporting evidence can be stored, but it must be marked `unsupported` and rendered as such. It is never silently dropped or silently promoted.

## ADR-015 — Epistemic labels and verification status

**Claim kinds (spec):** `FACT` (directly stated by a source), `OBSERVATION` (measured from data, e.g. a return), `INFERENCE` (reasoned from facts/observations), `SCENARIO` (conditional, forward-looking).

**Verification status:** `unverified → supported | uncertain | conflicting | unsupported`.

`conflicting` means at least one supporting and one contradicting evidence link exist. The report shows both sides and never resolves them silently.

Market moves are described as **Event Impact Analysis**: window returns, abnormal return vs benchmark, and alternative explanations. Never "X caused Y".

The existing ledger taxonomy (FACT / INTERPRETATION / CAUSAL_EXPLANATION / RISK / CONCLUSION) is mapped, not deleted, when the ledger migrates.

## ADR-016 — Configuration and secrets

- One typed `Settings` object (`pydantic-settings`), loaded from environment and `.env` in development only. Invalid config fails at startup with a clear message.
- `app.config` keeps exporting the same names during transition so existing modules don't change in Phase 1.
- Secrets only from environment or a secrets manager (AWS Secrets Manager in the deploy phase). Never committed. OpenClaw never receives `DATABASE_URL`, `JWT_SECRET_KEY`, or other credentials (existing rule, kept).

## ADR-017 — Observability

- **Now (Phase 1):** structured JSON logs to stdout with `service`, `env`, `version`, `request_id`, `job_id`. Health and readiness include DB + migration state.
- **Hardening phase:** OpenTelemetry traces across API → worker → MCP → LLM (the SDK is already pinned and optional); Prometheus metrics (`/metrics`) for request latency, job durations by stage, queue depth, tool error rate, LLM tokens/cost; Grafana dashboards checked into the repo.
- LangSmith stays optional for prompt-level debugging; it is not the metrics system.

## ADR-018 — Testing and evaluation strategy

| Tier | Runs against | Where |
|---|---|---|
| Unit | Pure functions, no I/O | `backend/tests/unit/` (new) + existing suite |
| Integration | Real Postgres (compose locally, service container in CI); migrations applied | `backend/tests/integration/` (new), marker `integration` |
| Contract | API request/response schemas | integration tier |
| Agent eval | Frozen fixtures, temporal cutoff, manually reviewed labels | `evals/` |
| Load | Job submission and read paths | Hardening phase (Locust or k6) |

- LLM and external tools are mocked in unit/integration tests. Real calls only in explicitly marked eval runs.
- Eval metrics follow the spec (retrieval, entity, relationship, citation accuracy, completeness, unsupported-claim rate, reliability). **No metric is reported until it has been measured on a reviewed dataset.** The dataset grows toward 100–300 manually reviewed events; the current 25 are the seed.

## ADR-019 — Python runtime

Standardize on **Python 3.11** locally, in Docker, and in CI. That removes the 3.9-only workarounds over time (the minimal MCP JSON-RPC client can be replaced by the official SDK later, not in Phase 1).
