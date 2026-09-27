# QuantPilot AI — Autonomous Investigation Engine

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=next.js&logoColor=white)](https://nextjs.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-1.5-FF6B35)](https://www.trychroma.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o--mini-412991?logo=openai&logoColor=white)](https://openai.com/)
[![Vercel](https://img.shields.io/badge/Deploy-Vercel-000000?logo=vercel&logoColor=white)](https://vercel.com/)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?logo=railway&logoColor=white)](https://railway.app/)

**QuantPilot** is a flagship systems project: an evidence-driven market and technology intelligence platform. It detects events, maps company/technology relationships, measures related market impact, and uses specialized agents to produce source-backed research — not stock-price predictions or investment recommendations. The desk is built to be explainable: workflow, agents, evidence, and the stack are visible on the page.

---

## What it does

- **Event-led research** — Detect a material event, collect evidence, verify claims, and write a structured report
- **Evidence Ledger** — Ranked, cited claims with a verification / Devil's Advocate pass
- **Multi-agent research** — News, company, market, filings, and verification agents (LangGraph)
- **Fact vs inference** — Grounded facts stay separated from model interpretation
- **Not a trading desk** — No portfolio OS, broker sync, investment memos, or price-alert recommendations

![Dashboard](./assets/dashboard.png)

---

## Architecture overview

```
Browser (Next.js) → FastAPI → LangGraph / investigation runner → MCP → yfinance / SEC / OpenAI
                                      ↓
                         Postgres/SQLite + Evidence Ledger + ChromaDB
```

Workflow: **Event → Entity → Relationship → Market → Research → Verification → Report**

Full diagram: **[ARCHITECTURE.md](./ARCHITECTURE.md)** · Current state, decisions and roadmap: **[docs/CURRENT_STATE.md](./docs/CURRENT_STATE.md)** · **[docs/ARCHITECTURE_DECISIONS.md](./docs/ARCHITECTURE_DECISIONS.md)** · **[docs/IMPLEMENTATION_ROADMAP.md](./docs/IMPLEMENTATION_ROADMAP.md)**

---

## Tech stack

**Shipped in this repo**

| Layer | Technology | Purpose |
|-------|------------|---------|
| Frontend | Next.js 14, TypeScript, Tailwind | Investigation desk, API client |
| Backend | Python, FastAPI, Pydantic | REST API, jobs, schemas |
| Agents | LangGraph, OpenAI | Multi-agent research + verification |
| Store | PostgreSQL / SQLite, optional Redis, ChromaDB | Ledger, cache, report search |
| Tools | MCP, yfinance, SEC EDGAR | Market data and filings |
| Auth | JWT + bcrypt | Persist research history |
| Quality | Pytest, GitHub Actions, Docker | Tests, CI, local/prod images |

**Ingestion pipeline (Phase 2, in progress; compose profile `pipeline`):** Kafka 4.3 (KRaft, single node) as the event transport, a lake writer consuming into a write-once raw lake on MinIO/S3, and run/event tracking with lineage in Postgres. Three real source adapters feed it through one tracked publisher: Massive (formerly Polygon.io) ticker news and daily bars (a free API key is needed), and the SEC's keyless company/ticker/exchange file. Adapters only fetch and normalize, and the raw lake keeps each provider object verbatim. The deterministic SYNTHETIC source remains for tests and is flagged as such. A PySpark 4.1 batch layer (compose profile `spark`, `local[*]` in one container, S3A to MinIO) turns raw objects into validated, deduplicated, date-partitioned Parquet under `processed/`. It uses explicit schemas, write-once reject objects for every failed check, a manifest per job, and tracking in `processing_jobs`; reruns are idempotent (`make spark-news|spark-market|spark-company|spark-all`). A canonical loader (compose profile `load`, pyarrow, no Spark) moves the processed Parquet listed in a completed job's manifest into Postgres: `companies` (keyed by CIK), `market_bars` and `news_articles` + `sources`. It validates every row before writing anything, applies the same newest-wins rule as Step 2.2 through database-enforced upserts, and commits in batches together with the ledger update. It records its counts and status on `processing_jobs`, plus a load report per attempt. Replays are no-ops (`make load-company|load-market|load-news|load-all`). Today only the real SEC companies are loaded. The dev lake holds no real market or news data (no Massive key is configured), and synthetic rows are refused by design. Research does not read these tables yet. Operators inspect the path with `make pipeline-status`, `pipeline-check` (stale RUNNING jobs, report only), `pipeline-reconcile` and `pipeline-quality`. `GET /metrics` is Prometheus text (low-cardinality labels only). `GET /ready` reports Kafka and the lake without requiring them. An optional compose profile `observability` runs Prometheus against `/metrics`. See [docs/PHASE2_PLAN.md](./docs/PHASE2_PLAN.md), [docs/PHASE2_7_PLAN.md](./docs/PHASE2_7_PLAN.md) and [docs/PHASE2_8_PLAN.md](./docs/PHASE2_8_PLAN.md).

**Spec next (not shipped yet):** a Spark cluster (only `local[*]` exists) / Airflow, Neo4j, pgvector, Terraform, AWS, Grafana. Prometheus scrape of `/metrics` is current; Grafana is not. Do not treat target tech as shipped.

---

## Phase breakdown

| Phase | Focus | Key deliverables |
|-------|--------|------------------|
| **1** | Foundation | FastAPI, yfinance, SEC EDGAR, health checks |
| **2** | Multi-agent pipeline | 5 agents, LangGraph, `POST /api/analysis/{ticker}` |
| **3** | Memory | ChromaDB, session history, watchlist routes |
| **4** | MCP tools | `mcp_server/`, stdio client, agent refactor to tools |
| **5** | Frontend | Dark dashboard, report UI, citations, watchlist sidebar |
| **6** | Reliability | Confidence, validation, facts/insights, logging, workflow runs |
| **7** | Data layer | PostgreSQL (SQLite local), JWT auth, user portfolios, migrate watchlist off JSON |
| **8** | Portfolio & scheduling | Portfolio Agent, APScheduler daily briefings, in-app Briefing panel |
| **9** | Alerts & Redis | Redis cache/queues (memory fallback), smart alerts, Alerts panel |
| **10** | Deeper research | Earnings, Macro, Verification agents in LangGraph (10-agent pipeline) |
| **11** | Browser MCP | OpenClaw-backed IR / shareholder letter tools + allowlisted HTTP fallback |
| **11.1** | Portfolio Sync | Broker-agnostic position import (paste or OpenClaw existing-session snapshot) with real share-weighted analysis |
| **12** | Ship polish | Investment Memo agent, LangSmith/OTel/Sentry hooks, GitHub Actions CI |
| **PRD v3 · 7** | Evidence Ledger | `investigations` / `claims` / `evidence_items` / links + Investigate UI shell |
| **PRD v3 · 8** | Reactive investigate | Move detect + Investigation Planner + MCP collect + weighted hypotheses (`/investigate`, `/run`) |
| **PRD v3 · 9** | Verification layer | Ground claims to evidence (FR-3) + Devil's Advocate pass that can demote the lead (FR-4) |
| **PRD v3 · 10** | Browser MCP + OpenClaw | IR/shareholder evidence in investigations; thin-API escalation; httpx primary + optional OpenClaw IR fallback |
| **PRD v3 · 11** | Scheduling & triggers | Idiosyncratic / vol-adjusted gates, equity vs ETF evidence paths, holdings sweep job + Scan my holdings |
| **PRD v3 · 12** | Proactive monitoring | Materiality-gated investigation notifications, Redis investigation job queue, Alerts → Evidence Ledger |
| **PRD v3 · 13** | Roster & observability | Earnings/Macro/Memo on investigations; `/health` + `/api/observability/status`; CI frontend build |
| **PRD v3 · FR-10** | Ledger NL search | Search prior investigations/claims/evidence (`GET /api/investigations/search` + Evidence Ledger search box) |

---

## Local development

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/quantpilot.git
cd quantpilot
```

### 2. Backend (port 8000)

Python 3.11 (see `.python-version`).

```bash
cd backend
uv venv --python 3.11 .venv          # or: python3.11 -m venv .venv
uv pip install -r requirements-dev.txt  # runtime + pytest
cp .env.example .env
# Edit .env — OPENAI_API_KEY, SEC_EDGAR_USER_AGENT
cd .. && make migrate && make api       # alembic upgrade head, then uvicorn --reload
```

Schema changes go through Alembic (`backend/alembic/versions/`); the API never runs `create_all`. In development the API also migrates on startup (`DB_AUTO_MIGRATE`). `GET /ready` reports `db_revision` and `migrations_current`.

**Tests.** `make test` runs the full suite on a throwaway SQLite file. `make db && make test-integration` runs the Postgres tier (`backend/tests/integration/`: migration drift, core repositories) against `quantpilot_test`, which the compose Postgres creates on first boot.

**Compose.** `make up` starts pgvector-enabled Postgres 16, Redis, a one-shot `migrate` service, the API and the frontend; `make dev` adds API hot reload. Host ports are overridable (`POSTGRES_PORT`, `REDIS_PORT`). pgvector is available in the image but not used by any table yet.

The MCP server is **auto-spawned** by the backend over stdio — no separate terminal required for normal use.

### 3. Frontend (port 3000)

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open **http://localhost:3000** — search `AAPL` to run the full pipeline.

### 4. Optional: MCP server standalone

```bash
python mcp_server/server.py
```

API docs: **http://localhost:8000/docs**

---

### 5. Optional: ingestion pipeline (Kafka → lake writer → MinIO)

```bash
make pipeline-up             # postgres, migrate, MinIO, Kafka (+ topics), lake-writer
make publish-synthetic n=5   # SYNTHETIC market events through the tracked publisher
make sources-check           # provider health: configured / reachable / auth (separate from API /health)
make ingest-company          # SEC company/ticker/exchange (keyless; needs SEC_EDGAR_USER_AGENT)
make ingest-market MARKET_SYMBOLS=AAPL,MSFT MARKET_START=2026-09-01   # Massive daily bars (MARKET_API_KEY)
make ingest-news NEWS_TICKERS=AAPL MAX_RECORDS=200                    # Massive news (NEWS_API_KEY)
make kafka-check             # topics + lake-writer consumer-group lag
make dlq                     # dead letters (read-only)
make test-kafka test-s3      # pipeline test tiers (isolated test topics/buckets)
```

Every ingest run is bounded (explicit window, record cap, page cap), throttled to the provider's limit, retried only for timeouts/429/5xx, and tracked in `ingestion_runs`. Rerunning the same window yields duplicates, not new events. Guarantees are at-least-once transport with idempotent, write-once sinks, not exactly-once. Details are in [docs/PHASE2_4_PLAN.md](./docs/PHASE2_4_PLAN.md). Set `POSTGRES_PORT`, `KAFKA_PORT` or `MINIO_PORT` if the default host ports are taken.

## Environment variables

See **[ENV_VARIABLES.md](./ENV_VARIABLES.md)** for the full list (backend + frontend).

---

## Deployment

Step-by-step Railway and Vercel instructions: **[DEPLOYMENT.md](./DEPLOYMENT.md)**

Quick summary:

1. Deploy `backend/` to Railway with env vars from `ENV_VARIABLES.md`
2. Deploy `frontend/` to Vercel with `NEXT_PUBLIC_API_URL=<Railway URL>`
3. Add Vercel URL to Railway `ALLOWED_ORIGINS` and redeploy backend

---

## Key engineering decisions

**LangGraph over a simple chain** — Research is multi-stage with shared state. Explicit nodes make it possible to attach validation, confidence, and logging per step without entangling five prompts in one script.

**MCP for tools** — Agents call `get_stock_price`, `get_stock_news`, etc. through a protocol boundary. Tools can be tested, swapped, or reused outside the web app (CLI, IDE plugins) without duplicating yfinance/SEC clients.

**ChromaDB for memory** — Reports are long-form text worth retrieving semantically (“prior AAPL analyses mentioning margin”). Local Chroma keeps the portfolio demo self-contained; production should use Chroma Cloud.

**Confidence scoring** — Heuristic scores signal *data quality* to users and downstream risk logic. When SEC filings are stale or news is thin, confidence drops — honest UX for a research copilot.

**Facts vs insights** — Recruiters and risk officers care about provenance. API-sourced metrics and headlines are tagged as facts; narrative sections are tagged as model-generated insights.

**Defensive validation** — Pydantic validators fix malformed agent JSON with defaults and warnings. The pipeline **never** hard-fails on a single bad LLM response — critical for demos and production resilience.

---

## What I learned

- Designing **stateful agent graphs** is as much about failure modes and observability as about prompts
- **MCP** is a practical way to share tools across agents without a tangled import graph
- Separating **facts from insights** early makes the UI and API more trustworthy than a single prose blob
- **Portfolio-grade** means deployment docs and env discipline, not just features

---

## Future improvements

- Real-time streaming of agent steps (SSE / WebSockets)
- CrewAI for parallel news / financial / SEC agents
- ~~Earnings / Macro / Verification agents (Phase 10)~~
- ~~Browser MCP + OpenClaw (Phase 11)~~
- ~~Portfolio Auto-Sync Agent — broker position import via paste or OpenClaw (Phase 11.1)~~
- ~~Investment Memo Agent + LangSmith/OTel/Sentry + CI (Phase 12)~~
- ChromaDB Cloud for durable vector memory on Railway
- Email delivery for alerts/briefings
- Dip Watch — alert-triggered re-analysis with buy/wait/avoid cards

---

## Project structure

```
quantpilot/
├── backend/           # FastAPI, agents, LangGraph, memory, observability
├── frontend/          # Next.js dashboard
├── mcp_server/        # MCP financial tools (stdio)
├── assets/            # Screenshots and demo media (see README_ASSETS.md)
├── ARCHITECTURE.md
├── DEPLOYMENT.md
└── ENV_VARIABLES.md
```

---

## License

MIT — see repository license file. Not financial advice; for educational and portfolio purposes.
