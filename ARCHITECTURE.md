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
│   LANGGRAPH WORKFLOW          │     │   MEMORY LAYER                     │
│   fetch_data → 5 agents       │     │   ChromaDB (vector reports)        │
│   sequential StateGraph       │     │   Session history (JSON)           │
└───────────────┬───────────────┘     │   Watchlist (JSON)                 │
                │                     └───────────────────────────────────┘
                ▼
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

**Railway (FastAPI)** — REST API gateway, CORS, exception handling, and background tasks (e.g. persisting reports after analysis). Hosts the LangGraph runtime and spawns the MCP server as a subprocess.

**LangGraph workflow** — `StateGraph` orchestrates six nodes: data fetch, then news → financial → SEC → risk → report. State carries agent outputs, confidence scores, validation warnings, and `facts_and_insights`.

**Memory layer** — ChromaDB stores embedded report text for semantic search; JSON files back session history and watchlist. Designed for local dev; production should use managed vector storage.

**AI agents** — Specialized prompts per domain. Each agent returns structured JSON validated by Pydantic; failures degrade to safe fallbacks without crashing the graph.

**MCP server** — Model Context Protocol tools (`get_stock_price`, `get_stock_fundamentals`, `get_stock_news`, `get_recent_filings`, etc.) decouple agents from raw API clients.

**External APIs** — Yahoo Finance (yfinance), SEC EDGAR (httpx), and OpenAI for generation and embeddings.

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

## Observability (Phase 6)

- JSON structured logs → `backend/logs/quantpilot.log`
- In-memory workflow runs → `GET /api/observability/runs`
- Per-agent confidence scores and `facts_and_insights` on every analysis response

---

## Request flow (analysis)

1. User submits ticker on Vercel UI  
2. `POST /api/analysis/{ticker}` on Railway  
3. WorkflowRun created; LangGraph executes six nodes  
4. MCP tools fetch market + SEC data; OpenAI structures each agent output  
5. Validators apply safe defaults; confidence scored; facts separated from insights  
6. Response returned; report saved to ChromaDB in background  
7. UI renders report, confidence meter, citations panel  
