# QuantPilot — Current State (repository audit)

Audited: 2026-09-25, against `QuantPilot_Master_Build_Specification.pdf` (source of truth).
Baseline commit `4cf2b4e` plus an **uncommitted** cleanup that removed the personal trading desk (portfolio, memos, ideas, briefings, price-alert rules).

All numbers below were measured from the working tree, not estimated.

> Snapshot taken **before Phase 1**. For what Phase 1 changed (Alembic, typed settings, core schema, test tiers), see "Phase 1 — as built" in [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md).

| Area | Size |
|---|---|
| `backend/app` | 109 Python files, ~21,100 lines |
| `backend/tests` | 26 files, ~8,350 lines, **262 tests passing** |
| `mcp_server` | 10 files, ~2,240 lines |
| `evals` | 31 files, ~3,060 lines, 25 curated cases |
| `frontend` | 4 routes, 47 components, ~8,300 lines TS/TSX |

---

## 1. Current architecture

```
Browser (Next.js 14, 4 routes)
   │ REST + JWT or X-Guest-Id
   ▼
FastAPI monolith (backend/app)  ── one process does everything:
   ├── HTTP routers (auth, stocks, filings, analysis, investigations, memory, dashboard, alerts, observability)
   ├── Investigation pipeline (synchronous, inside the request)
   │     investigation_runner.py ── optional LangGraph investigation_graph.py (flag)
   ├── Deep-research pipeline: LangGraph analysis_graph.py (10 sequential agent nodes)
   ├── APScheduler (in-process) ── investigation sweep job
   ├── MCP client ──stdio subprocess──► mcp_server/ (yfinance, SEC EDGAR, httpx IR fetch, optional OpenClaw)
   ├── SQLAlchemy ──► SQLite (default) or PostgreSQL
   ├── ChromaDB (local disk) ── report + ledger vector search
   └── Redis (optional; in-memory fallback) ── cache, queue, sweep lock
```

Properties that matter for the target architecture:

- **Monolith.** API, orchestration, scheduling, and data access run in one process. No worker tier.
- **Synchronous research.** `POST /api/investigations/investigate` and `POST /api/analysis/{ticker}` block until the pipeline finishes. There is an SSE stream for analysis, but no job ID pattern.
- **Ticker-centric and owner-scoped.** Every investigation belongs to one `owner_key` and one ticker. There is no shared knowledge base of companies, technologies, sources, or relationships.
- **External data only through MCP tools.** A good boundary, but it is invoked inline by the request.

## 2. Existing functionality

| Capability | Where | Notes |
|---|---|---|
| Market-move detection with benchmark residual and volatility z-score | `services/move_detector.py`, `trigger_logic.py`, `event_detection.py` | Closest thing to the spec's Event Impact Analysis. Uses yfinance, single benchmark (SPY default). |
| Investigation gate / materiality scoring | `investigation_gate.py`, `materiality.py` | Still has `portfolio_exposure` inputs (always 0 now). |
| Investigation pipeline | `investigation_runner.py` (~1,250 lines), `workflows/investigation_graph.py` (~1,000 lines) | plan → collect via MCP → assess → rank hypotheses → verify → Devil's Advocate → persist. |
| Evidence Ledger | `evidence_ledger_store.py`, tables `investigations`, `claims`, `evidence_items`, `claim_evidence_links` | Claims link to evidence with `supports / contradicts / context`. |
| Verification + Devil's Advocate | `verification_engine.py`, `investigation_verification_agent.py`, `devils_advocate_agent.py` | Can demote the leading claim. |
| Company memory | `company_memory.py`, table `company_memory_items` | Append-only, owner-scoped, status lifecycle (ACTIVE → CONTRADICTED / OUTDATED). |
| Deep research report | `workflows/analysis_graph.py` | news → financial → SEC → earnings → macro → risk → bull → bear → verification → report. |
| Research / ledger chat (RAG) | `research_chat.py`, `investigation_chat.py`, `investigation_search.py` | ChromaDB + keyword fallback. |
| Scheduled sweep | `jobs/investigation_sweep_job.py` | Iterates tickers in legacy `holdings` table. |
| Material-investigation notifications | `investigation_notifications.py`, `alert_events` | In-app only. |
| Evaluation harness | `evals/` | 25 cases, temporal cutoff, baseline + ablations, deterministic replay. |
| Hardening | `security/`, `readiness.py`, `runtime.py` | Rate limits, LLM budget, request IDs, prompt-injection sanitizer, draining shutdown. |

**Not present anywhere:** ingestion pipeline, source registry, entity extraction, relationship graph, technology entities, async job service, data lake, streaming, batch processing, lineage records.

## 3. Existing technologies

| Layer | In repo today | Spec target |
|---|---|---|
| Frontend | Next.js 14, TypeScript, Tailwind, GSAP, Lenis, Three/Vanta | Next.js, TypeScript, Tailwind ✅ |
| API | FastAPI 0.128, Pydantic 2, uvicorn | FastAPI ✅ |
| Language runtime | **Python 3.9 local venv, 3.11 in Docker and CI** | — (mismatch) |
| Primary DB | SQLite default, PostgreSQL 16 in compose | PostgreSQL ✅ (partial) |
| Migrations | **None** — `create_all` + hand-written `ALTER TABLE` | — |
| Vector search | ChromaDB 1.5 (local disk) | pgvector ❌ |
| Cache / queue | Redis 5 client, in-memory fallback | — |
| Scheduling | APScheduler 3.10 in-process | Airflow ❌ |
| Streaming | — | Kafka ❌ |
| Data lake | — | S3 + Parquet ❌ |
| Processing | — | PySpark / Structured Streaming ❌ |
| Graph | — | Neo4j ❌ |
| Agents | LangGraph 0.6, OpenAI | LangGraph ✅ |
| Tools | Custom MCP stdio server + minimal JSON-RPC fallback (MCP SDK needs 3.10+) | — |
| Browser | httpx allowlisted fetch; optional OpenClaw | — |
| Observability | JSON logs, request IDs, in-memory workflow tracker; optional LangSmith / Sentry / OTel | OpenTelemetry, Prometheus, Grafana (partial) |
| Infra | Dockerfile, docker-compose, Railway/Vercel configs | Docker ✅, Terraform ❌, AWS ❌ |
| CI | GitHub Actions: pytest, tsc, lint, build | GitHub Actions ✅ |
| Tests | Pytest, pytest-asyncio | + integration, load, agent eval (partial) |

## 4. Existing database / schema

Created by `Base.metadata.create_all()` in `backend/app/db/session.py`, then patched by four `_migrate_*_columns()` functions that issue `ALTER TABLE ... ADD COLUMN` and log-and-continue on failure.

| Table | Cols | Purpose | Status vs new direction |
|---|---|---|---|
| `users` | 4 | Auth | Keep |
| `portfolios` | 4 | Legacy portfolio | **Out of scope** — still referenced by sweep and anonymous-user bootstrap |
| `holdings` | 8 | Legacy watchlist/positions | **Out of scope** — sweep reads tickers from here |
| `briefings` | 13 | Legacy daily briefing | **Dead** — no code writes it now |
| `alert_rules` | 10 | Legacy price alerts | **Dead** — no routes now |
| `alert_events` | 14 | Notifications (kept for material investigations) | Keep for now |
| `analysis_runs` | 8 | Per-owner deep-research history; has `recommendation` column | Refactor later |
| `investigations` | 15 | Evidence Ledger case | Keep; later superseded by `research_jobs` |
| `claims` | 9 | Hypotheses; `claim_type` = FACT / INTERPRETATION / CAUSAL_EXPLANATION / RISK / CONCLUSION | Keep; taxonomy differs from spec |
| `evidence_items` | 10 | Evidence per investigation; `raw_payload_json`, `content_hash` | Keep; not deduplicated across cases |
| `claim_evidence_links` | 6 | Claim ↔ evidence stance | Keep |
| `company_memory_items` | 19 | Longitudinal memory per owner + ticker | Keep |

Schema-level observations:

- Integer autoincrement IDs everywhere. Fine inside one DB; awkward as cross-system IDs (Kafka keys, S3 paths, Neo4j nodes).
- Structured payloads are stored as JSON strings in `Text` columns (`da_outcome_json`, `roster_json`, `raw_payload_json`, `extra_json`).
- No `sources` table. A URL cited in two investigations is two unrelated `evidence_items` rows.
- No `companies`, `technologies`, `events`, or `relationships`. "Company" is a ticker string.
- `owner_key` scopes everything, including facts that should be global (a filing is the same filing for every user).

## 5. Existing agent architecture

There are **three orchestration paths** with overlapping agents:

1. **Investigation runner** (`investigation_runner.py`) — linear Python: detect move → planner (LLM picks MCP tools) → collect → hypothesis ranker → verification → Devil's Advocate → roster (earnings + macro) → persist → notify.
2. **Investigation graph** (`workflows/investigation_graph.py`, on when `INVESTIGATION_GRAPH_ENABLED=true`) — LangGraph: `load_memory → planner → create_tasks → research → assess ⇄ identify_gaps → rank → verify → challenge ⇄ identify_gaps → synthesize`. Conditional loops are bounded by `MAX_INVESTIGATION_ITERATIONS` / `MAX_CHALLENGE_LOOPS`.
3. **Deep-research graph** (`workflows/analysis_graph.py`) — LangGraph, 10 sequential nodes: news, financial, SEC, earnings, macro, risk, bull, bear, verification, report.

Agents in `backend/app/agents/`: news, financial_metrics, sec_filing, earnings, macro, risk, bull, bear, verification, report, investigation_planner, hypothesis_generator, hypothesis_ranker, investigation_verification, devils_advocate. Shared LLM wrapper in `agents/llm.py` enforces the per-investigation call/token budget.

Mapping to the spec's six agents:

| Spec agent | Closest existing | Gap |
|---|---|---|
| News Agent | `news_agent` | Ticker news summarizer; no event extraction. |
| Company Agent | `financial_metrics_agent`, `sec_filing_agent`, `earnings_agent` | Split across three; no strategy / partnership / investment analysis. |
| Technology Agent | — | Missing. |
| Market Agent | `move_detector` + `trigger_logic` (not an agent) | No related-company movement, no event windows. |
| Evidence Agent | `investigation_verification_agent`, `verification_engine` | Checks claims inside one case; no cross-source retrieval. |
| Master Agent | `report_agent`, `synthesize` node | No gap resolution across agents. |

Bull/bear debate and the risk agent are **equity-thesis framing**. `FinalReportOutput.recommendation` still exists and the validator normalizes it to BUY / HOLD / SELL. That conflicts with the spec ("not an investment recommendation engine").

## 6. Existing APIs / tools

**HTTP API (39 routes, measured from the running app):**

- Health: `GET /health`, `GET /ready`
- Auth: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- Market data passthrough: `GET /api/stocks/{ticker}`, `GET /api/filings/{ticker}`
- Deep research: `POST /api/analysis/{ticker}`, `POST /api/analysis/{ticker}/stream` (SSE), `POST /api/chat/{ticker}`
- Evidence Ledger: `GET|POST /api/investigations`, `POST /api/investigations/investigate`, `GET|DELETE /api/investigations/{id}`, `POST .../{id}/run|claims|evidence|links|complete|chat|smart-summarize`, `GET /api/investigations/search`, `GET /api/investigations/trigger/{ticker}`, `POST /api/investigations/sweep`, `GET /api/investigations/memory/{ticker}[/related]`
- Memory: `GET /api/memory/history|reports/{ticker}|search|tickers`
- Dashboard: `GET /api/dashboard`, `GET /api/companies/{ticker}/intelligence`
- Notifications: `GET /api/alerts/events`, `POST /api/alerts/events/{id}/read|feedback`
- Observability: `GET /api/observability/status`, `GET /api/observability/runs[/{id}]` (auth)
- `POST /api/smart-summarize`

No `/v1` versioning. No job resources.

**MCP tools (`mcp_server/server.py`):** `get_stock_price`, `get_stock_fundamentals`, `get_stock_news`, `get_price_history`, `get_recent_filings`, `get_company_facts`, `get_ir_materials`, `fetch_browser_page`, `get_shareholder_letter`, `browser_search`, `browser_open`, `browser_find_document`, `browser_download`, `browser_extract`.

**OpenClaw / browser:** `mcp_server/tools/openclaw_client.py` (HTTP or CLI), used only as an optional fallback for IR documents. Primary path is allowlisted httpx. Webpage text is sanitized as untrusted before it reaches prompts.

## 7. Existing tests

- 262 tests, all passing (`PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q`, ~32 s).
- File names follow two historical phase schemes (`test_investigation_phaseN*.py`, `test_phaseN_*.py`). They don't map to the new roadmap.
- Each test file sets `DATABASE_URL` to its own SQLite file **at import time**, before importing the app. Results depend on import order, and no test runs against PostgreSQL.
- External calls (MCP, LLM) are mocked with `unittest.mock`. No integration tests against real Postgres, Redis, or MCP subprocess. No load tests.
- `evals/` is a separate deterministic harness (25 cases, ablations). Last measured run is recorded in `evals/reports/last_run.md`. Some metrics (evidence relevance 0.040) are limited by strict phrase-overlap scoring, not source quality.
- CI (`.github/workflows/ci.yml`): backend pytest on 3.11; frontend `tsc`, lint, build.

## 8. Current strengths

- **Evidence-first data model already exists.** Claims link to evidence with a stance, and the Devil's Advocate can demote the lead. This is the spec's accuracy-first idea in miniature.
- **Clean external-data boundary.** All market/filing/web access goes through MCP tools with timeouts, retries, and untrusted-text sanitization.
- **Real hardening primitives.** Request-ID correlation, JSON logs with secret redaction, `/health` and `/ready`, rate limiting, per-investigation LLM budgets, Redis lock with TTL, draining shutdown.
- **Event-impact seed.** Benchmark residual and volatility z-score logic is a genuine starting point for abnormal-return windows.
- **Honest evaluation culture.** Temporal cutoff, ground truth kept out of production functions, baseline comparison, no invented metrics.
- **Green test suite and CI**, plus a Docker Compose stack with Postgres and Redis.

## 9. Technical debt

Ordered by how much it blocks the target architecture.

1. **No migration system.** `create_all` plus ad-hoc `ALTER TABLE` that logs and continues on failure. Schema drift is silent. Blocks every data phase.
2. **SQLite is the default database** and the only database tests use. Postgres-specific features (JSONB, pgvector, partial indexes, `ON CONFLICT`) can't be tested.
3. **Python version split** — 3.9 locally, 3.11 in Docker/CI. Forces `from __future__` workarounds and a hand-rolled MCP JSON-RPC client because the MCP SDK needs 3.10+.
4. **Configuration is module-level `os.getenv` globals** in `app/config.py` (200+ lines). No validation, no typed settings object, defaults evaluated at import. `pydantic-settings` is pinned but unused. Some defaults are surprising (`INVESTIGATIONS_SWEEP_ENABLED` defaults to `true`).
5. **Long-running work inside HTTP requests.** Investigation and analysis block the request. No job ID, no progress resource, no retry of partial failure.
6. **In-process scheduler.** APScheduler runs in every API replica; only the Redis lock prevents duplicate sweeps.
7. **Missing core domain.** No companies, technologies, sources, events, relationships. Evidence is duplicated per investigation. Global facts are owner-scoped.
8. **Three overlapping orchestration paths** (runner, investigation graph, analysis graph) with a feature flag choosing between two of them. `investigation_runner.py` is ~1,250 lines.
9. **Recommendation output still exists** (`FinalReportOutput.recommendation`, BUY/HOLD/SELL normalization, `analysis_runs.recommendation`, UI badge). Conflicts with the spec.
10. **Claim taxonomy mismatch** — FACT / INTERPRETATION / CAUSAL_EXPLANATION / RISK / CONCLUSION vs the spec's Fact / Observation / Inference / Scenario.
11. **Legacy portfolio schema still wired in** — `portfolios`, `holdings` (used by sweep and guest bootstrap), dead `briefings` and `alert_rules` tables, legacy `watchlist.json` import code.
12. **JSON-in-Text columns** instead of typed columns or JSONB.
13. **ChromaDB on local disk** — not durable in containers, not transactional with Postgres, separate backup story.
14. **Workflow tracker is in memory**; logs also write to `backend/logs/` inside the container.
15. **Documentation drift** — `ARCHITECTURE.md` still describes 5 agents and a watchlist. Fourteen `docs/PHASE_*.md` files use a numbering scheme that will collide with the new roadmap.
16. **Uncommitted working-tree cleanup** (trading-desk removal) needs to land as its own commit before Phase 1.

## 10. Missing components relative to the target architecture

| Spec component | Status |
|---|---|
| Alembic (or equivalent) migrations | Missing |
| Core entities: companies, technologies, sources, events, relationships, market events, research jobs, agent runs, reports | Missing (claims/evidence exist in ledger form) |
| Async research jobs (`POST /research` → 202 + `job_id`, `GET /research/{id}`, progress stages) | Missing |
| Worker tier (news / market / document workers) | Missing |
| Kafka event backbone, DLQ | Missing |
| S3 data lake (raw / processed / curated), Parquet | Missing |
| Spark batch and Structured Streaming | Missing |
| Airflow DAGs | Missing (APScheduler instead) |
| Neo4j intelligence graph and traversals | Missing |
| pgvector | Missing (ChromaDB instead) |
| Entity extraction + entity resolution | Missing |
| Event impact windows (−1D, +1D, +3D, +5D, +20D), abnormal return vs benchmark, alternative explanations | Partial (single-window residual) |
| Technology Agent, Master Agent, Evidence Agent across sources | Missing / partial |
| Data lineage (output → claim → evidence → source → raw object) | Missing |
| Idempotency keys on jobs and ingestion | Partial (content hash, event fingerprint) |
| Prometheus metrics, Grafana dashboards | Missing |
| Terraform, AWS deployment | Missing |
| Evaluation set of 100–300 manually reviewed events | 25 cases |
| Load tests | Missing |

## 11. Components that should be refactored (not rewritten)

| Component | Refactor | When |
|---|---|---|
| `app/config.py` | Typed `pydantic-settings` object; keep `app.config` names as a compatibility shim | Phase 1 |
| `app/db/session.py` | Engine/session from settings; replace `create_all` + ALTERs with Alembic | Phase 1 |
| `app/observability/logger.py` | Add `service` / `env` / `version` fields; stdout only in containers | Phase 1 |
| Test DB setup | Shared fixtures; Postgres integration tier alongside existing SQLite unit tests | Phase 1 |
| `docker-compose.yml` | pgvector-capable Postgres image, one-shot migrate service, dev override | Phase 1 |
| Report `recommendation` field and BUY/HOLD/SELL normalization | Remove or rename to a neutral summary | Needs approval (see roadmap) |
| Claim taxonomy | Map to Fact / Observation / Inference / Scenario | When research claims move to the new tables |
| `investigation_runner.py` + `investigation_graph.py` | Consolidate on one LangGraph path; split into stage modules | Research-jobs phase |
| Agent roster | Evolve toward News / Company / Technology / Market / Evidence / Master | Multi-agent phase |
| Sweep + APScheduler | Replace with Airflow DAG or worker trigger; tracked tickers stop depending on `holdings` | Orchestration phase |
| ChromaDB | Migrate to pgvector | RAG phase |
| `ARCHITECTURE.md`, `docs/PHASE_*.md` | Archive old phase docs under `docs/history/`; keep one current architecture doc | Phase 1 (docs only) |

## 12. Components that should remain unchanged (for now)

- **`mcp_server/`** and `app/mcp_client.py` — the external-data boundary works and is tested. Revisit only when ingestion workers replace inline collection.
- **`app/security/`** — sanitizer, redaction, rate limit, LLM budget, request context.
- **Evidence Ledger routes and tables** — they power the current desk. New core tables live alongside; the ledger is migrated later, not broken now.
- **`evals/` harness structure** — temporal cutoff, isolation, ablations. Extend the dataset; don't rewrite the harness.
- **Frontend desk shell** (routes, HUD primitives, design system).
- **CI workflow skeleton** — extend with a Postgres service, don't replace.
- **`/health` and `/ready` contracts** — extend readiness, keep response shape.
