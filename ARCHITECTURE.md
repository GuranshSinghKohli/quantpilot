# QuantPilot AI — Architecture

## System diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER BROWSER                                   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ HTTPS
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              VERCEL — Next.js 14 Frontend (Dashboard)                    │
│   SearchBar · AgentWorkflow · ReportDisplay · Watchlist · Citations      │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ REST (JSON)  NEXT_PUBLIC_API_URL
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              RAILWAY — FastAPI Backend                                   │
│   /api/stocks · /api/filings · /api/analysis · /api/memory · /api/watchlist │
│   /api/observability/runs · structured logging · CORS                    │
└───────────────┬─────────────────────────────────────┬───────────────────┘
                │                                     │
                ▼                                     ▼
┌───────────────────────────────┐     ┌───────────────────────────────────┐
│   LANGGRAPH WORKFLOW          │     │   MEMORY / DATA LAYER               │
│   fetch_data → agents         │     │   PostgreSQL (users, portfolios,    │
│   sequential StateGraph       │     │     holdings / watchlist)           │
└───────────────┬───────────────┘     │   ChromaDB (vector reports)         │
                │                     │   Session history (in-memory)       │
                ▼                     └───────────────────────────────────┘
┌───────────────────────────────┐
│   5 AI AGENTS (GPT-4o-mini)   │
│   news · financial · SEC      │
│   risk · report               │
│   + confidence & validation   │
└───────────────┬───────────────┘
                │ tool calls via stdio
                ▼
┌───────────────────────────────┐
│   MCP SERVER (mcp_server/)    │
│   6 financial tools           │
└───────────────┬───────────────┘
                │
                ▼
┌───────────────────────────────┐
│   EXTERNAL DATA APIs          │
│   yfinance · SEC EDGAR        │
│   OpenAI (LLM + embeddings)   │
└───────────────────────────────┘
```

## Layer descriptions

**User browser** — Single-page dashboard for ticker search, live agent progress, research reports, and portfolio sidebar utilities.

**Vercel (Next.js)** — React UI with typed API client, timeout handling, and environment-based backend URL. Static/SSR deployment with zero server management.

**Railway (FastAPI)** — REST API gateway, CORS, JWT auth, exception handling, and background tasks (e.g. persisting reports after analysis). Hosts the LangGraph runtime and spawns the MCP server as a subprocess. Phase 7 adds PostgreSQL for users, portfolios, and watchlist holdings.

**LangGraph workflow** — `StateGraph` orchestrates data fetch, then news → financial → SEC → earnings → macro → risk → bull → bear → verification → report → investment memo (Phase 12). State carries agent outputs, confidence scores, validation warnings, and `facts_and_insights`.

**Memory / data layer** — PostgreSQL (or local SQLite) stores users, portfolios, holdings, daily briefings, alert rules, and alert events. Redis (optional; in-memory fallback) caches quotes and queues alert evaluation jobs. ChromaDB stores embedded report text for semantic search. Session history remains in-process memory. APScheduler runs daily briefings and interval alert checks.

**AI agents** — Specialized prompts per domain. Each agent returns structured JSON validated by Pydantic; failures degrade to safe fallbacks without crashing the graph. Phase 12 adds an Investment Memo agent that compresses the full report into a shareable decision brief (`decision`, conviction, thesis, catalysts, risks).

**MCP server** — Model Context Protocol tools (`get_stock_price`, `get_stock_fundamentals`, `get_stock_news`, `get_recent_filings`, `get_ir_materials`, `snapshot_active_browser_tab`, etc.) decouple agents from raw API clients. Phase 11 adds browser/IR tools that prefer OpenClaw when configured and fall back to allowlisted HTTP fetches. Phase 11.1 adds a Portfolio Auto-Sync flow: the Portfolio Sync agent (`app/agents/portfolio_sync_agent.py`) extracts `{ticker, shares, avg_cost}` from a pasted brokerage positions page or an OpenClaw existing-session snapshot of the user's own signed-in browser — no credentials ever pass through QuantPilot — and the Portfolio Analyzer then weights the basket by real position sizes instead of equal-weight.

**External APIs** — Yahoo Finance (yfinance), SEC EDGAR (httpx), optional OpenClaw browser control for IR pages, and OpenAI for generation and embeddings.

---

## Why each technology was chosen

### LangGraph

Multi-step research is inherently **stateful**: each agent depends on prior outputs. LangGraph provides explicit nodes, edges, and typed state — easier to debug and extend than a single prompt chain. Confidence scoring, validation, and observability hooks attach per node without rewriting agent logic.

### CrewAI (future)

CrewAI excels at **role-based parallel crews**. QuantPilot uses a **sequential** pipeline today (risk synthesizes news + financial + SEC). A future phase could run news/financial/SEC in parallel via CrewAI while keeping LangGraph as the top-level orchestrator.

### MCP (Model Context Protocol)

Financial tools are shared between agents and potentially other clients (IDE, CLI). MCP standardizes tool schemas, keeps API keys out of prompts, and mirrors how production AI platforms expose capabilities to models.

### FastAPI

Async-first Python, automatic OpenAPI docs, Pydantic integration, and mature middleware ecosystem. Fits LangGraph + httpx + background tasks in one deployable service.

### Next.js

App Router, TypeScript, and Vercel-native deployment. The dashboard needs fast iteration on UI components (workflow visualization, report layout) without coupling to the Python backend.

### ChromaDB

Lightweight local vector store for **report memory** and semantic search — no extra infrastructure for demos. Embeddings via OpenAI align with existing API keys. Production path: Chroma Cloud or Pinecone.

### Railway

Python monorepo deploy with minimal config (`Procfile`, `railway.toml`), persistent env vars, and public HTTPS URL for the API. Good fit for portfolio backends that are not serverless-friendly (long-running analysis, MCP subprocess).

### Vercel

Optimized for Next.js frontends: preview deployments, env injection at build time, and global CDN. Frontend stays stateless; all intelligence lives on Railway.

---

## Observability (Phase 6 + Phase 12)

- JSON structured logs → `backend/logs/quantpilot.log`
- In-memory workflow runs → `GET /api/observability/runs`
- Per-agent confidence scores and `facts_and_insights` on every analysis response
- Phase 12 optional exporters (env-gated, safe no-ops when unset):
  - **LangSmith** — `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`
  - **Sentry** — `SENTRY_DSN`
  - **OpenTelemetry** — `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT`
- GitHub Actions CI runs backend pytest + frontend `tsc` / lint on every PR

---

## Request flow (analysis)

1. User submits ticker on Vercel UI  
2. `POST /api/analysis/{ticker}` on Railway  
3. WorkflowRun created; LangGraph executes fetch + 10 agent nodes  
4. MCP tools fetch market + SEC data; OpenAI structures each agent output  

5. Validators apply safe defaults; confidence scored; facts separated from insights  
6. Response returned; report saved to ChromaDB in background  
7. UI renders report, confidence meter, citations panel  
