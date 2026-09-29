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

**Decision (Phase 3.1, shipped).** Postgres `entities`, `entity_aliases`, `relationships`, and `relationship_evidence` remain canonical. Neo4j is an optional, idempotent projection (`python -m app.graph project`) keyed on `entity_id` / `relationship_id`. Kafka `relationship.upserted` is still future. Graph queries serve traversal questions; depth 1 is answered from Postgres.

**Why.** Every relationship must trace to evidence and support retraction. Doing that transactionally is simpler in Postgres. A projection can always be rebuilt; a primary graph store can't be rebuilt from anything.

**Amended (Phase 3.1).** Compose profile `graph` runs Neo4j 5 community. The API starts if Neo4j is down. `/ready` reports `neo4j` and does not fail on it. See `docs/PHASE3_1_PLAN.md`, `docs/GRAPH_SCHEMA.md`, ADR-025.

**Amended (Phase 3.2).** Tests use a separate `graph-test` Neo4j (`localhost:17687`). Reconciliation is read-only. Repair is opt-in. Relationship candidates are not graph edges until accepted. See `docs/PHASE3_2_PLAN.md`, ADR-026.

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

**Amended (Phase 2 Step 2.4, shipped).** The first Kafka use is the ingestion backbone, not the research job queue. Topics are `quantpilot.{news,market,company}.raw` (key `event_id`) and `quantpilot.ingestion.dlq` (key `dlq_id`), created from `Settings` by `python -m app.pipeline kafka init`. Messages carry the full typed record (well under 1 MB); the claim-check pattern above is deferred until filings/full documents are ingested. See ADR-020 and `docs/PHASE2_4_PLAN.md`.

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

## ADR-020 — Effectively-once ingestion over at-least-once Kafka

**Status: Accepted, shipped in Phase 2 Step 2.4.**

**Problem.** Ingestion events cross three systems (Kafka, the object store, Postgres) that share no transaction. Kafka transactions only cover Kafka→Kafka, so end-to-end exactly-once delivery is not available.

**Decision.** At-least-once transport, made safe by idempotency at every sink:
- **Producer:** librdkafka idempotent producer (`acks=all`), key = `event_id`. An event counts as delivered only on an error-free delivery report; `ingestion_records` moves to `published` with the Kafka partition/offset at that point, never on `produce()`.
- **Lake writer:** dedicated consumer group, manual commits. Offsets are committed only after the raw object is written (write-once `put_if_absent`) and `ingestion_records` is `landed`. A redelivery finds the identical object and is a replay. Different bytes under the same key, or the same event at a different key, is an `integrity_conflict` dead letter; nothing is overwritten.
- **DLQ:** only permanent failures or exhausted retries. It is published and acknowledged before the offset commit; if the DLQ is unavailable, nothing is committed. `dlq_id` is deterministic per failed delivery, so a redelivered failure is recognisable as a copy.
- **Statuses** only move forward (Step 2.2), so replays never regress an event.

**Rejected.** Transactional outbox (not needed while the tracking row is written before the publish and retried on RETRY claims). Exactly-once claims. Auto-commit consumers.

**Cost.** Duplicate messages are possible in Kafka and in the DLQ, so consumers must stay idempotent. A crash-looping lake writer (unexpected error) stalls its partition rather than dropping data.

## ADR-021 — Source adapters: fetch + normalize only, Massive + SEC

**Status: Accepted, shipped in Phase 2 Step 2.5.**

**Problem.** Real providers differ in auth, pagination, rate limits, identifiers and failure modes. That variety must not leak into the transport (Kafka), storage (lake) or canonical repositories built in Steps 2.2–2.4.

**Decision.**
- A `SourceAdapter` only fetches (`streams → pages → SourceItem(original)`) and normalizes (`normalize(item) → contract record`). It never publishes, stores, deduplicates or touches the database. `IngestionRunner` turns records into events (`build_event`, with the verbatim provider object as `original`) and hands them to the existing tracked `EventPublisher`.
- All HTTP goes through one `SourceClient`: client-side throttle, then status classification, then the pipeline's `call_with_retry`. Retried: timeout, connection error, 408, 5xx, and 429 (honouring Retry-After up to a bound). Never retried: 400/404/422, 401/403 (which also stop the run), and a malformed 2xx body.
- An explicit `(domain, provider)` registry builds adapters and names missing settings. There are no dynamic imports.
- Providers:
  - Massive (formerly Polygon.io) for news (`/v2/reference/news`) and daily bars (`/v2/aggs`). Both are documented, on the free plan, use cursor pagination and have stable ids.
  - SEC `company_tickers_exchange.json` for companies: keyless, with CIK identity.
  - yfinance stays an API tool and is not an ingestion source, because it is unofficial and undocumented and has no status-code contract.
- Identity comes from the contracts (`event_id = sha256(source | external_id | content_hash)`), so rerunning a window produces duplicates. Cursors are not persisted across runs, on purpose: rerunning the window is the resume mechanism.

**Rejected.**
- Adapters that publish directly (couples source code to Kafka).
- Per-provider retry logic (inconsistent budgets).
- Persisted cursors / checkpoint tables (state without extra safety, given deterministic ids).
- Scraping.

**Cost.**
- The news and market adapters share one free Massive key limited to 5 requests/minute, so runs are slow by design and must not run concurrently.
- The free plan has 2 years of history and end-of-day data only.
- Massive bars carry no currency and (with `adjusted=false`) no adjusted close; both are left `None`.

## ADR-022 — PySpark batch processing: raw lake → processed Parquet

**Status: Accepted, shipped in Phase 2 Step 2.6.**

**Problem.** Raw objects are one JSON event per object, write-once, partitioned by arrival date. Research and canonical loading need typed, validated, deduplicated data partitioned by record date, with every rejected record accounted for and every row traceable to its raw object. Reruns must not create duplicates, and raw must never change.

**Decision.**
- PySpark **4.1.3** in `local[*]` inside a dedicated `spark` image (Java 17; `hadoop-aws` 3.4.2 + AWS SDK v2 bundle, checksum-pinned). The API never imports Spark; the CLI (`python -m app.pipeline process`) and the spark test tier are the only entry points.
- Input is an explicit list of raw keys planned through `ObjectStore.list` per ingestion day (or a prefix / key list), capped. Spark reads those URIs with an explicit all-string wire schema (PERMISSIVE), so a bad value gets a named check instead of an inferred type or a silent null.
- An ordered list of documented checks assigns each row its first failure. Failures become write-once reject objects (`rejects/<domain>/…/<event_id>.json`); `input_objects = valid + rejected` is enforced.
- Dedup: exact `event_id` duplicates → one row; an `event_id` already processed keeps the existing row; newest `ingested_at` per contract `dedup_key` wins. Existing rows of the affected partitions are merged in, `localCheckpoint`-ed and written back with dynamic partition overwrite, so reruns are logical no-ops.
- One tracker: `processing_jobs` (migration `0006` adds input window end, prefix, object count and Spark app id), a write-once manifest per job, and `ingestion_records` → `processed` for rows the job added. A failure is recorded `FAILED` and re-raised. One job per output prefix (Postgres advisory lock).
- Credentials never enter the Spark conf; S3A reads the standard `AWS_*` variables.

**Rejected.**
- Spark 3.5 (Hadoop 3.3 S3A on the end-of-life AWS SDK v1).
- Schema inference, Python UDFs, reading `raw/` as a directory.
- Reject Parquet per job (a rerun would duplicate rejects); overwriting rejects.
- Delta/Iceberg, Structured Streaming, a Spark cluster and Airflow in this step.
- Reading raw through the Python `ObjectStore` (all bytes through the driver).

**Cost.**
- 8–43 s per job on this laptop for 5 objects, almost all fixed overhead (S3A copy-based commits per partition, a dozen small actions).
- Merge-on-read rewrites each affected partition in full.
- News versions whose `published_at` moved between days are not collapsed across partitions.
- Output is deterministic logically, not byte-for-byte: Parquet file names contain a write UUID.

## ADR-023 — Canonical loading: processed Parquet → Postgres

**Status: Accepted, shipped in Phase 2 Step 2.7.**

**Problem.** Processed Parquet is deduplicated per job but is not the system of record (ADR-002). Rows must reach `companies`, `market_bars` and `news_articles` + `sources` under three guarantees:
- the Step 2.2 newest-wins rule, with no second versioning implementation;
- no duplicates on replay, retry or a crash after commit;
- no silent loss of invalid rows.

**Decision.**
- A separate CLI process (`python -m app.pipeline load`, `make load-*`) in the pipeline image, using pyarrow only. There is no Spark, Kafka, HTTP or FastAPI startup in the path.
- Input is only the files listed in a `COMPLETED` job's write-once manifest, selected by job, manifest or `--latest` per domain, with an optional date filter. Each file's existence and size are checked against the manifest. A file rewritten by a later job fails with `ManifestMismatch` rather than loading different data.
- Validation runs over the whole selection before anything is written:
  - the schema;
  - required fields;
  - `event_id` and `dedup_key` recomputed from the record;
  - the raw key and URI;
  - the registered source for the domain;
  - synthetic data refused;
  - domain rules (OHLC ordering, http(s) URLs, the ticker list);
  - lineage, meaning the event is in `ingestion_records` and the job exists.

  Any problem fails the load with every problem listed.
- Writes go through the existing `app/repositories/canonical.py` upserts:
  - `ON CONFLICT … DO UPDATE WHERE newer_than_existing` for bars and articles;
  - `SELECT … FOR UPDATE` plus the same version comparison for companies, keyed by CIK and never by name.

  Spark's tie-break was aligned to the same rule (the larger `event_id` wins).
- One transaction per batch. It never spans files and includes the `ingestion_records → loaded` ledger update. Rows that fail on a constraint are retried once after all batches; anything left marks the load `CANONICAL_LOAD_FAILED`.
- Load state is kept on the existing `processing_jobs` row (migration `0007`): status, attempts and counts (candidates, inserted, updated, stale, duplicates, failed). There is also a write-once report per attempt at `_manifests/loads/<job>/<attempt>.json`. Reconciliation invariants are checked before `COMPLETE`.
- The loader takes the same Postgres advisory lock as Spark processing for the domain's output prefix.

**Rejected.**
- A new load-tracking table, since `processing_jobs` already has one row per job.
- Loading directly from raw data or Kafka.
- Scanning `processed/` without a manifest.
- COPY into a staging table, which adds a second write path with no measured need at current volumes.
- Skip-and-continue on invalid rows.
- Loading synthetic data outside test databases.

**Cost.**
- Idempotent, not exactly-once: a crash after a batch commits leaves the job `RUNNING`, and the rerun reports those rows as duplicates.
- Only the latest job per domain is reliably loadable after partition overwrites.
- Automatic ticker reassignment is not attempted.
- Measured throughput (PHASE2_7_PLAN §11): about 900 rows/s for market bars and 1,000 rows/s for companies on this laptop.

## ADR-024 — Pipeline observability without a second control plane

**Status: Accepted, shipped in Phase 2 Step 2.8.**

**Problem.** After Steps 2.1–2.7 the data path works, but an operator cannot answer "what arrived, where did it go, what is stuck" without reading tables by hand. Adding a dashboard, a second lineage store, or auto-failing jobs would hide the existing state machines.

**Decision.**
- Reuse `app/pipeline/metrics.py`. Enforce a closed label set and a 64-series cap. Expose `GET /metrics` on the existing API. Prometheus is an optional compose profile; the API does not depend on it.
- Keep `/health` as process-alive plus the existing cheap DB/Redis flags. `/ready` still fails only on DB/migrations/draining, and *reports* Kafka and the lake.
- `correlation_id` stays the event-envelope field (default `run_id`). Job-level Spark/load logs bind it (or the job id when there is no single event).
- CLI: `status`, `check` (stale RUNNING, report only), `reconcile` (scoped), `quality`, `dlq --summary`, guarded `retry-load` / `reprocess-job` / `retry-ingestion`.
- Retention is documented, not automated. Raw lake objects are never deleted by this step.

**Rejected.**
- Grafana, alertmanager, a new tracing product.
- Auto-failing stale jobs or auto-replaying the DLQ.
- High-cardinality Prometheus labels (`event_id`, ticker, URL, job_id).
- Making Massive/SEC part of `/ready`.

**Cost.**
- One-shot CLI jobs only expose process-local counters for that process. `/metrics` on the API is HTTP metrics plus DB gauges.
- Kafka/lake down is visible on `/ready` but does not fail it.

## ADR-025 — Entity graph: Postgres first, Neo4j derived

**Status: Accepted, shipped in Phase 3.1.**

**Problem.** Phase 2 loaded canonical companies. Intelligence needs directed, evidenced relationships and a traversable graph without inventing edges from co-mention or making Neo4j the system of record.

**Decision.**
- Reuse `entities` / `relationships` / `relationship_evidence`. Add `entity_aliases`, identity columns, `provenance_kind`, and `graph_projections` (migration `0008`).
- Resolution is exact (CIK, source key, normalized name, ticker). Ambiguity is returned. No fuzzy merge.
- New relationships go through `RelationshipCandidate` → one service that requires `source_id` + `evidence_id`. INFERRED is never treated as fact.
- Neo4j is optional. Projector MERGEs on `entity_id` / `relationship_id`. Postgres commit is independent of Neo4j.
- Read API is parameterized and bounded. No client Cypher.

**Rejected.**
- Parallel `graph_entities` / `graph_relationships` tables.
- LLM / article extraction in this phase.
- Making Neo4j required for `/ready` or API startup.

## ADR-026 — Graph reliability and relationship candidates

**Status: Accepted, shipped in Phase 3.2.**

**Problem.** Phase 3.1 projection was additive and shared the developer Neo4j with tests. Stale nodes accumulated. There was no candidate stage before a canonical edge.

**Decision.**
- Disposable `neo4j-test` container (profile `graph-test`). Tests refuse `localhost:7687`.
- `graph reconcile` is read-only ID comparison. `graph repair` upserts missing/mismatched state. Deletion requires `--allow-delete --confirm-delete`.
- `relationship_candidates` is a staging table. Acceptance uses `persist_relationship`. Rejected candidates never reach Neo4j.
- Spark-test defaults `TEST_KAFKA_BOOTSTRAP=kafka:29092`; host Kafka tests stay on `localhost:9092`.

**Rejected.**
- Automatic Neo4j deletes during reconcile.
- Fuzzy entity matching or LLM relationship extraction.
- Using the developer Neo4j volume as the test database.

## ADR-027 — Market and event intelligence is descriptive

**Status: Accepted, shipped in Phase 3.3.**

**Problem.** Canonical news, market bars, and the entity graph existed, but there was no deterministic layer that turned structured sources into events, session windows, and benchmark-relative observations.

**Decision.**
- Reuse `events` / `event_entities`. Add `event_candidates` and `event_impacts` (migration `0010`).
- News articles stay documents unless they have structured tickers; those become `NEWS` candidates, not auto-accepted economic events.
- Entity associations use the Phase 3.2 resolver. Ambiguous/unresolved tickers are never guessed.
- Trading windows are session offsets from actual `market_bars`. v1 uses unadjusted `close`. Missing data is `NULL`.
- `relative_return` is a simple benchmark-adjusted proxy (`MARKET_BENCHMARK_SYMBOL`), not CAPM and not causal.
- Confirmed events project as `:CanonicalEvent-[:INVOLVES]->:Entity`. The graph remains derived.

**Rejected.**
- LLM event extraction, RAG, pgvector, LangGraph research over impacts.
- Stock prediction or investment recommendations.
- Forward-filling prices or silently substituting another benchmark.

## ADR-028 — Retrieval is a package, not an agent

**Status: Accepted, shipped in Phase 4.1.**

**Problem.** Future research agents must not search arbitrary tables or invent context.

**Decision.**
- Reuse `sources` / `evidence_records`. Add derived `retrieval_documents`, `retrieval_chunks`, `chunk_embeddings`, `document_entities` (migration `0011`).
- Enable pgvector on the existing Postgres. Exact cosine search for v1. SQLite stores JSON vectors for unit tests.
- Embeddings go through `EmbeddingProvider`. Tests use a deterministic fake lexical hasher. OpenAI embeddings are optional at runtime.
- `ContextAssembler` returns a bounded, cited EvidencePackage. Semantic failure is explicit; structured Postgres facts still assemble.
- Neo4j is not queried. Agents are not implemented in this phase.

**Rejected.**
- LangGraph / LLM extraction / report generation in 4.1.
- Making pgvector the document source of truth.
- High-cardinality metric labels (query text, document_id, chunk_id).

## ADR-029 — Research agents consume packages, not tables

**Status: Accepted, shipped in Phase 4.2.**

**Problem.** Future (and current) agents must not bypass retrieval or invent evidence.

**Decision.**
- New `app/research/` LangGraph with a **fixed** topology. Investigation and analysis graphs stay unchanged.
- Agents receive an EvidencePackage and a closed tool list that wraps existing retrieval services.
- `LLMProvider` + `FakeLLMProvider`. Tests never call OpenAI. `/health` does not require an LLM key.
- Reuse `research_jobs` / `agent_runs` / `research_claims` and `ClaimKind` / `VerificationStatus`. No new migration.
- EvidenceAgent is a deterministic hallucination guard. Confidence cannot override status.
- MarketAgent reports existing impact windows only. No buy/sell/hold, no causation, no prediction.

**Rejected.**
- Autonomous browsing, CrewAI, MCP research tools.
- LLM-created graph edges or relationship extraction.
- A second vector database or a competing evidence table.

## ADR-030 — Bounded master research, verified report

**Status: Accepted, shipped in Phase 4.3.**

**Problem.** A research request needs a plan, a subset of the existing specialists, and a report that cannot promote unsupported text into a fact.

**Decision.**
- Same `app/research/` graph. Nine fixed nodes. The model fills `ResearchPlan` and may repeat already-verified summary lines.
- Unknown or oversized plans fall back to the documented keyword/package router.
- Report assembly is deterministic. Unmatched summary lines are dropped.
- Conflicts stay conflicts. Inference and scenario stay labeled.
- Persist the report on the existing `reports` table. No migration past `0011`.
- `run_research()` stays synchronous and worker-callable. No Kafka research topic in this phase.

**Rejected.**
- A second research endpoint, a second LLM abstraction, or a new claims table.
- Letting the model add tools, nodes, citations, or investment advice.
- Treating FakeLLM evaluation numbers as model quality.

## ADR-031 — Async research uses an outbox and the existing graph

**Status: Accepted, shipped in Phase 4.4.**

**Problem.** `POST /api/research` ran the whole graph inside the request. A crash between a database commit and a Kafka publish could lose or double-submit work.

**Decision.**
- `POST` inserts `research_jobs` plus `research_outbox` in one transaction and returns `publish_pending` until the request event is published.
- Topics `quantpilot.research.request`, `quantpilot.research.completed`, and `quantpilot.research.dlq` are created by `kafka init`. The message key is `research_id`.
- The worker calls `run_research()`. It commits the database before the Kafka offset.
- Duplicate delivery is ignored once a job is `succeeded`, `partial`, or `failed`.
- A PostgreSQL row update is the lease. Live leases are not stolen.
- Guarantee written in the docs: at-least-once delivery with idempotent processing.

**Rejected.**
- Describing the pipeline as a stronger delivery guarantee than at-least-once with idempotent processing.
- Putting the EvidencePackage or credentials on Kafka.
- A second job runner or a second Kafka client stack.
- Automatic DLQ replay in this phase.

## ADR-032 — Research quality is fixture evaluation, not a new agent

**Status: Accepted, shipped in Phase 4.5.**

**Problem.** The research path needs a repeatable way to see where fixture behavior fails. Adding another agent would not measure that.

**Decision.**
- Keep the Phase 4.3 graph and the Phase 4.4 outbox worker.
- `python -m app.research evaluate` still prints the 24-case baseline. `--suite` adds retrieval, claims, reports, planner, async, and security fixture checks.
- Claim precision, recall, and F1 compare expected finding types. They are not human-judged statement accuracy.
- Retrieval scores using `FakeEmbeddingProvider` are labeled lexical fixture embeddings.
- A document that contains instruction-like text stays document text. Tool permissions do not change. This is defense in depth, not a claim of immunity.
- `/ready` does not depend on the LLM provider.
- No migration. Alembic stays at `0012`.

**Rejected.**
- A sentiment, risk, recommendation, forecast, or portfolio agent.
- Treating FakeLLM or lexical-hash scores as production model quality.
- A load-test framework. Ten sequential submissions are a fixture timing, not capacity.

## ADR-033 — Date windows and planner routing are explicit

**Status: Accepted, shipped in Phase 4.6.**

**Problem.** Phase 4.5 measured date-filter accuracy 0.0 and an unnecessary-agent rate near 0.55. Both were fixture-scoring bugs in the measurement and the router, not a reason to add another model.

**Decision.**
- Publication filters use `published_at`. Event filters use `occurred_at`. `retrieved_at` is not a filter column.
- Windows are half-open UTC: `start <= timestamp < end`. A date-only end covers that whole civil day.
- Naive API timestamps are rejected. Values read back from SQLite are treated as UTC because the offset is not stored.
- The deterministic planner classifies the request text. Evidence-package text does not add a specialist.
- `relevant_agents` remains the Phase 4.5 tag so the old unnecessary-agent rate stays comparable. Required, optional, and forbidden agents are a separate fixture label. Those rates are fixture-based routing correctness, not planner intelligence.
- No migration. Alembic stays at `0012`.

**Rejected.**
- Changing the evaluator so the old open-ended date probe would pass.
- Selecting a specialist because a document, entity, or market row exists.
- A new agent, or a change to the async worker.

## ADR-034 — Quote explicit technology text; do not invent a specialist

**Status: Accepted, shipped in Phase 4.7.**

**Problem.** Phase 4.6 finding-type recall fell from 1.0 to 0.8824 on 24 cases and 0.9167 on 50 cases. "Meta data center accelerators" selected technology, and the source says accelerators were deployed, but the fixture reader only quoted excerpts containing "technolog", "chip", or "gpu".

**Decision.**
- The fixture reader quotes a document when the excerpt names a technology, chip, GPU, or accelerator. The statement is the source text. It does not synthesize USES, a partnership, or a customer edge.
- A discussion or a co-mention stays a quotation or produces no technology finding. Mention is not USES.
- "AMD Instinct Meta data centers" selects no specialist because the request has no domain vocabulary. That omission stays in finding-type recall and is recorded as `AGENT_NOT_SELECTED`. The planner is not changed to read package text.
- `claim_recall` remains finding-type overlap. `supported_claim_recall` counts explicit facts the selected specialist should quote. Empty expectations are correctly omitted unsupported claims.
- No migration. Alembic stays at `0012`.

**Rejected.**
- Deleting the Instinct case or clearing its expected FACT type to restore 1.0.
- Selecting news because the package contains an article.
- Turning "discussed" or a co-mention into USES or PARTNERED_WITH.



