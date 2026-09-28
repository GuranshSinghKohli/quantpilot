# QuantPilot — Implementation Roadmap

Incremental phases aligned to the spec's 16-week plan. Each phase ships something runnable and testable, updates docs and the on-desk stack panel (`frontend/lib/stack.ts`), and introduces a technology only when that phase has a workload for it.

Decisions referenced as ADR-xxx live in `ARCHITECTURE_DECISIONS.md`. Baseline facts live in `CURRENT_STATE.md`.

| Phase | Spec weeks | Theme | New technology |
|---|---|---|---|
| 0 | — | Land the pending cleanup commit | — |
| **1** | 1–2 | **Foundation** | Alembic, pydantic-settings (activated), pgvector-capable Postgres image |
| 2 | 3 | Source registry + raw landing | MinIO/S3, ingestion workers |
| 3 | 4 | Event backbone | Kafka (+ DLQ) |
| 4 | 5 | Batch data platform | Airflow, Spark, Parquet |
| 5 | 6 | Entities + relationships (Postgres SoR) | Phase 3.1 started: aliases + resolver + evidenced writes |
| 6 | 7 | Intelligence graph | Phase 3.1 started: optional Neo4j projection |
| 7 | 8–9 | Market event impact analytics | — |
| 8 | 10 | Async research jobs | Worker tier (Postgres queue → Kafka) |
| 9 | 11 | Spec agents + RAG | pgvector (replaces ChromaDB) |
| 10 | 12 | Verification layer v2 | — |
| 11 | 13–14 | Hardening + observability | OpenTelemetry, Prometheus, Grafana |
| 12 | 15 | Evaluation (100–300 reviewed events) + load tests | Locust/k6 |
| 13 | 16 | Deployment | Terraform, AWS |

---

## Phase 0 — Land the pending cleanup

The working tree holds an uncommitted, tested removal of the trading-desk surfaces (262 tests passing, `tsc` clean). Commit it on its own so Phase 1 starts from a clean, reviewable baseline.

---

## Phase 1 — Foundation (detailed)

**Goal.** A repo where schema changes are migrations, config is typed and validated, Postgres is the dev and integration-test database, and the minimal core domain exists as tables and models. **No new user-facing features. No Kafka, S3, Spark, Airflow, Neo4j, or pgvector usage.** Existing routes and the UI keep working unchanged.

### Phase 1 — as built

Differences between the plan below and what shipped:

- **Migration chain.** `0001_baseline` (the 12 pre-Alembic tables), `0002_drop_legacy_briefings_alert_rules`, `0003_core_foundation`, `0004_analysis_runs_headline` (renames `recommendation` → `headline`; buy/hold/sell output removed from reports).
- **Adopting old databases.** Instead of a manual `alembic stamp`, `app.core.migrations.upgrade_to_head` detects a pre-Alembic database, adds the columns the old ALTERs would have added, creates any missing legacy tables and indexes, stamps `0001` and upgrades. On adopted databases, seven of the ALTER-added columns stay nullable and SQLite lacks the `alert_events.investigation_id` foreign key. Fresh databases have neither gap.
- **Startup.** `DB_AUTO_MIGRATE` (unset = on only in development). When it is off and the database is behind, the API still serves, but the scheduler doesn't start and `/ready` reports `migrations_current: false`. Railway runs `alembic upgrade head` before uvicorn, and compose runs a one-shot `migrate` service.
- **Idempotency.** Research-job keys are unique per owner, not globally. Reusing a key for a different request raises `IdempotencyConflict`.
- **Defaults.** `INVESTIGATIONS_SWEEP_ENABLED` now defaults to `false`.
- **Test isolation.** `tests/conftest.py` pins a throwaway SQLite file before any app import. Earlier, the legacy suite could bind to `backend/data/quantpilot.db` depending on import order.

### 1.1 Runtime + repo hygiene
- **What.** Pin Python 3.11 (`.python-version`, README). Split `requirements.txt` (runtime) from `requirements-dev.txt` (pytest, ruff). Add a `Makefile` with the commands in "Local commands" below. Move `docs/PHASE_*.md` to `docs/history/` with a one-line index, and rewrite `ARCHITECTURE.md` to point at the current docs.
- **Why.** Removes the 3.9/3.11 split (ADR-019). Old phase numbering collides with this roadmap.
- **Test.** Full existing suite passes on 3.11.

### 1.2 Typed settings
- **What.** `backend/app/core/settings.py` with a `pydantic-settings` `Settings` class covering DB, Redis, auth, CORS, OpenAI, observability, and feature flags. `app/config.py` becomes a thin shim that re-exports the same names from `Settings` so existing imports keep working.
- **Why.** Validated config, one place to read it (ADR-016).
- **Test.** Unit tests: defaults, env override, invalid values fail fast, secrets never appear in `repr`.

### 1.3 Structured logging
- **What.** Move the JSON formatter to `app/core/structured_logging.py` (not `logging.py`, which would shadow the stdlib module); add `service`, `env`, `version` fields; stdout-only when `ENVIRONMENT != development`. Keep `get_logger` / `log_event` signatures.
- **Why.** Logs must be machine-joinable across API and future workers (ADR-017).
- **Test.** Unit test on formatter output and redaction.

### 1.4 Database + migrations
- **What.**
  - `app/core/db.py` builds engine and sessions from settings. `app/db/session.py` re-exports during transition.
  - Alembic in `backend/alembic/`. `0001_baseline` reproduces today's 12 tables exactly (verified by schema diff). Existing DBs get `alembic stamp 0001`.
  - Remove `create_all` and `_migrate_*_columns()` from app startup. Startup no longer mutates schema.
  - `/ready` adds `db_revision` and `migrations_current`.
- **Why.** ADR-002, ADR-003.
- **Test.** Integration: `upgrade head → downgrade base → upgrade head` on Postgres; baseline matches the models (no autogenerate diff).

### 1.5 Core domain schema (`0003_core_foundation`)
- **What.** SQLAlchemy models in `app/domain/` plus Pydantic schemas, for the tables in the "Phase 1 schema" section below. Minimal repository functions with idempotent upserts for `sources`, `events`, `companies`, `relationships`, `research_jobs`.
- **Why.** Every later phase writes to these tables. They are created now, empty, and exercised only by tests. **No route or pipeline writes to them in Phase 1.**
- **Test.** Integration: constraints (unique dedup keys, FK integrity, CHECK enums), idempotent upsert returns the same row twice, cascade and restrict behavior.

### 1.6 Docker dev environment
- **What.** Compose uses `pgvector/pgvector:pg16`, adds a one-shot `migrate` service (`alembic upgrade head`), and the backend depends on it completing. `docker-compose.dev.yml` overlay for hot reload (opt-in, so plain `docker compose up` stays production-shaped). Redis stays optional.
- **Why.** One command for a production-shaped local stack.
- **Test.** `make up && make ready` returns `migrations_current: true`.

### 1.7 Test infrastructure
- **What.** `backend/tests/conftest.py` with a Postgres fixture (`TEST_DATABASE_URL`), migrations applied once per session, a transaction rolled back per test. Pytest markers `unit` / `integration`. New tests go in `tests/unit/` and `tests/integration/`; the existing 262 stay where they are and keep their SQLite setup until touched. CI gains a Postgres service container and runs both tiers.
- **Why.** ADR-018. Postgres features must be tested on Postgres.
- **Test.** CI green on both tiers.

### 1.8 Service boundaries
- **What.** Document and create the target layering (ADR-012): `app/core`, `app/domain`, `app/repositories`. Existing `routers/`, `services/`, `agents/`, `workflows/` stay in place. A short `backend/app/README.md` explains which layer may import which.
- **Why.** New code lands in the right place from day one; old code moves when it's next touched.

### 1.9 Docs + on-desk stack
- **What.** Update README (run commands, Alembic), `ENV_VARIABLES.md` (settings), `frontend/lib/stack.ts` (Alembic and pgvector-capable Postgres image shipped; pgvector *usage* still "spec next").

### Phase 1 exit criteria
- `make up` brings up Postgres → migrate → API → frontend. `/ready` reports migrations current.
- Existing 262 tests pass on Python 3.11. New unit + integration tiers pass in CI against Postgres.
- `alembic upgrade head` / `downgrade base` round-trip cleanly.
- Core tables exist with constraints; nothing writes them from production code yet.
- No behavior change in existing routes or UI.

### Phase 1 schema (minimal, evolvable)

All new tables: `id UUID PK`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, and `updated_at` where rows mutate. Enums are `VARCHAR` + `CHECK` (cheaper to evolve than native Postgres enums).

**Naming.** Legacy ledger tables (`investigations`, `claims`, `evidence_items`, `claim_evidence_links`) stay untouched. New tables use distinct names (`research_claims`, `evidence_records`, `claim_evidence`) to avoid collisions until the ledger is migrated. This is flagged for approval.

```
entities            id, entity_type [company|technology|organization|person], canonical_name,
                    UNIQUE(entity_type, canonical_name)

companies           entity_id PK/FK→entities, legal_name, ticker NULL, exchange NULL,
                    cik NULL UNIQUE, website NULL, UNIQUE(ticker, exchange)

technologies        entity_id PK/FK→entities, slug UNIQUE, category NULL, description NULL

sources             id, source_type [news|filing|press_release|research|market_data|web],
                    publisher NULL, url, url_hash UNIQUE, title NULL,
                    published_at NULL, retrieved_at, content_hash, raw_uri NULL,
                    metadata JSONB

events              id, event_type [news|product_launch|technology_release|partnership|investment|
                    acquisition|funding|earnings|filing|regulatory|executive_change|market_move|other],
                    title, summary NULL, occurred_at NULL, published_at NULL, detected_at,
                    primary_source_id FK→sources, evidence_id NULL, dedup_key UNIQUE,
                    status [detected|confirmed|rejected]

event_entities      event_id FK, entity_id FK, role [subject|counterparty|target|acquirer|acquired|
                    investor|partner|supplier|customer|technology|mentioned|other],
                    source_id NULL, evidence_id NULL, confidence NULL,
                    PK(event_id, entity_id, role)

event_candidates    identity_key UNIQUE, event_type, title, source_id, evidence_id NULL,
                    status [PENDING|ACCEPTED|REJECTED|AMBIGUOUS], accepted_event_id NULL

event_impacts       (event_id, entity_id, symbol, calculation_version) UNIQUE,
                    session-window returns / volume / volatility, nullable when unavailable

relationships       id, subject_entity_id FK→entities, predicate [INVESTED_IN|PARTNERED_WITH|
                    SUPPLIES|COMPETES_WITH|USES|ACQUIRED|DEVELOPS|OWNS|CUSTOMERS_OF|DEPENDS_ON],
                    object_entity_id FK→entities,
                    status [asserted|supported|disputed|retracted], confidence NULL,
                    valid_from NULL, valid_to NULL, first_seen_at, last_seen_at,
                    UNIQUE(subject_entity_id, predicate, object_entity_id)

market_event_windows id, event_id FK→events, company_entity_id FK→entities, ticker,
                    benchmark_ticker, window_label [-1D|+1D|+3D|+5D|+20D],
                    window_start, window_end, return_pct NULL, benchmark_return_pct NULL,
                    abnormal_return_pct NULL, volume_change_pct NULL, volatility NULL,
                    data_source, method_version, computed_at,
                    UNIQUE(event_id, ticker, window_label, method_version)

research_jobs       id, owner_key, job_type [event_research|company_research|technology_research],
                    status [queued|running|succeeded|partial|failed|cancelled],
                    idempotency_key, UNIQUE(owner_key, idempotency_key), request JSONB, event_id NULL FK→events,
                    current_stage NULL, attempts, error NULL,
                    queued_at, started_at NULL, finished_at NULL

agent_runs          id, research_job_id FK, agent_name, stage, attempt,
                    status [running|succeeded|failed|skipped], model NULL, prompt_version NULL,
                    input_hash NULL, tokens_in NULL, tokens_out NULL, latency_ms NULL,
                    error NULL, started_at, finished_at NULL,
                    UNIQUE(research_job_id, agent_name, attempt)

research_claims     id, research_job_id FK, agent_run_id NULL FK, statement,
                    claim_kind [FACT|OBSERVATION|INFERENCE|SCENARIO],
                    verification_status [unverified|supported|uncertain|conflicting|unsupported],
                    confidence NULL, uncertainty_note NULL

evidence_records    id, source_id FK→sources, excerpt, locator JSONB (section/page/char span),
                    content_hash, extraction_method, extracted_at,
                    UNIQUE(source_id, content_hash)

claim_evidence      claim_id FK→research_claims, evidence_id FK→evidence_records,
                    stance [supports|contradicts|context], note NULL,
                    assessed_by_agent_run_id NULL FK→agent_runs, PK(claim_id, evidence_id)

relationship_evidence relationship_id FK, evidence_id FK, stance, PK(relationship_id, evidence_id)

reports             id, research_job_id FK, version, status [draft|final],
                    title, sections JSONB, UNIQUE(research_job_id, version)
```

Deliberately **not** in Phase 1: embeddings, raw document bodies, graph-sync state, ingestion-run bookkeeping. Each arrives with the phase that uses it.

---

## Phase 2 — Source registry + raw landing (week 3)
- MinIO in compose, S3 client wrapper, `raw/` layout (ADR-009).
- Ingestion workers (plain Python CLI) for news, SEC filings, and prices. They write the raw object, then upsert `sources` (idempotent by `url_hash` / `content_hash`).
- `ingestion_runs` table for lineage.
- Tests: idempotent re-ingest, raw object immutability.

## Phase 3 — Event backbone (week 4)
- Kafka (single broker locally) with the topics in ADR-010, a DLQ, JSON-schema-versioned messages.
- Ingestion workers publish `raw.document.ingested`. An event-extraction consumer writes `events` (dedup by `dedup_key`) and publishes `event.detected`.
- Tests: consumer idempotency under redelivery, DLQ after N failures.

## Phase 4 — Batch data platform (week 5)
- Airflow (local compose profile) with DAGs for the daily ingestion sweep and processed/curated Parquet builds. The APScheduler sweep is retired.
- First Spark job with a measured reason: historical price backfill and bulk window computation into `curated/market_windows`.

## Phase 5 — Entities + relationships (week 6)
- Entity extraction from evidence records and entity resolution (ticker / CIK / name normalization) into `entities`, `companies`, `technologies`.
- Relationship extraction writes `relationships` + `relationship_evidence`. Every edge has at least one evidence link or is marked `asserted`.

## Phase 6 — Intelligence graph (week 7)
- Neo4j projection fed by `relationship.upserted`. A rebuild-from-Postgres command. Traversal endpoints (read-only).

## Phase 7 — Market event impact analytics (weeks 8–9)
- Compute `market_event_windows` for −1D / +1D / +3D / +5D / +20D with benchmark and abnormal return, reusing the existing residual/z-score logic.
- Related-company comparison through the graph. Alternative explanations are surfaced (sector move, macro day, earnings). Copy stays "impact analysis", never causation.

## Phase 8 — Async research jobs (week 10)
- `POST /api/v1/research` → 202, `GET /api/v1/research/{id}`, SSE progress (ADR-013).
- Research worker consumes a Postgres queue first, then Kafka `research.job.requested`.
- The existing investigation pipeline runs inside the worker behind this contract. Old `/api/investigations/investigate` becomes a thin wrapper, then is deprecated.

## Phase 9 — Spec agents + RAG (week 11)
- LangGraph graph with News, Company, Technology, Market, Evidence, and Master agents, writing `agent_runs` and `research_claims`.
- pgvector embeddings over `evidence_records`; ChromaDB retired.
- The three current orchestration paths consolidate into this one.

## Phase 10 — Verification layer v2 (week 12)
- Claim extraction from drafts, citation validation against `evidence_records`, contradiction detection (`conflicting`), unsupported-claim flagging.
- Report renderer shows epistemic labels and both sides of conflicts.

## Phase 11 — Hardening + observability (weeks 13–14)
- Backoff/jitter everywhere, circuit breakers on external tools, cache policy, rate limits per route class.
- OpenTelemetry traces across API → worker → MCP → LLM; Prometheus `/metrics`; Grafana dashboards in repo.

## Phase 12 — Evaluation + load (week 15)
- Grow the manually reviewed eval set toward 100–300 historical events. Report the spec metrics only as measured.
- Load tests on job submission and read paths; publish results with the environment described.

## Phase 13 — Deployment (week 16)
- Terraform for AWS (RDS Postgres, S3, MSK or managed Kafka, ECS/EKS services, Secrets Manager). GitHub Actions deploy pipeline.
- Architecture diagram and demo script updated to match what actually shipped.

---

## Local commands

**Today (before Phase 1):**

```bash
# full stack
cp backend/.env.example backend/.env
docker compose up --build            # postgres, redis, backend :8000, frontend :3000

# or natively
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev

# checks
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q
frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
```

**After Phase 1 (planned):**

```bash
make up                 # docker compose: postgres → migrate → backend → frontend
make migrate            # alembic upgrade head
make revision m="..."   # alembic revision --autogenerate
make test               # unit tier (existing suite + tests/unit)
make test-integration   # Postgres-backed tier
make ready              # curl /ready → migrations_current: true
make down
```
