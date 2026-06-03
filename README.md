# QuantPilot AI — AI Quant Research Copilot

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.128-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=next.js&logoColor=white)](https://nextjs.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.6-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-1.5-FF6B35)](https://www.trychroma.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o--mini-412991?logo=openai&logoColor=white)](https://openai.com/)
[![Vercel](https://img.shields.io/badge/Deploy-Vercel-000000?logo=vercel&logoColor=white)](https://vercel.com/)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?logo=railway&logoColor=white)](https://railway.app/)

**QuantPilot AI** is a production-style research copilot that turns a stock ticker into an institutional-quality equity snapshot. A LangGraph multi-agent pipeline pulls live market data, SEC filings, and news via MCP tools, synthesizes risk, and delivers a structured research report with confidence scoring, fact/insight separation, and vector memory — all through a polished Next.js dashboard.

---

## What it does

- **One-click equity research** — Enter a ticker (e.g. `AAPL`) and receive a full multi-section report with executive summary and BUY/HOLD/SELL-style recommendation
- **Transparent agent pipeline** — Watch five specialized agents run in sequence: news, financial metrics, SEC filings, risk, and report synthesis
- **Grounded citations** — Facts (prices, filings, headlines) are separated from LLM-generated insights for auditability
- **Confidence scoring** — Heuristic per-agent and overall confidence based on data completeness — not a black box
- **Persistent memory** — Past reports stored in ChromaDB with semantic search, session history, and a watchlist
- **Production observability** — Structured JSON logs, workflow run tracking, and defensive validation that never crashes the pipeline

![Dashboard](./assets/dashboard.png)

---

## Architecture overview

```
Browser → Vercel (Next.js) → Railway (FastAPI) → LangGraph → Agents → MCP → yfinance / SEC / OpenAI
                                      ↓
                                 ChromaDB + Watchlist
```

Full diagram and technology rationale: **[ARCHITECTURE.md](./ARCHITECTURE.md)**

---

## Tech stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Frontend | Next.js 14, TypeScript, Tailwind | Dashboard UI, API client, deploy on Vercel |
| Backend | FastAPI, Pydantic, uvicorn | REST API, CORS, background tasks |
| Orchestration | LangGraph | Stateful multi-agent workflow |
| Agents | OpenAI GPT-4o-mini | News, valuation, SEC, risk, report synthesis |
| Tools | MCP (stdio server) | Standardized financial tool interface |
| Memory | ChromaDB, JSON stores | Vector report search, history, watchlist |
| Data | yfinance, SEC EDGAR | Market data and filings |
| Observability | JSON logging, workflow tracker | Agent timing, confidence, failures |
| Deployment | Railway (API), Vercel (UI) | Production hosting |

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
| **7** | Deployment | Railway + Vercel, README, architecture docs, portfolio polish |

---

## Local development

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/quantpilot.git
cd quantpilot
```

### 2. Backend (port 8000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — OPENAI_API_KEY, SEC_EDGAR_USER_AGENT
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

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
- Redis for production session and workflow persistence
- User authentication and saved portfolios
- ChromaDB Cloud for durable vector memory on Railway
- Backtesting hooks and price alert integrations

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
