# QuantPilot Finance MCP Server

Standalone MCP server (`quantpilot-finance`) that exposes Yahoo Finance and SEC EDGAR tools over **stdio**.

FastAPI agents call these tools through `backend/app/mcp_client.py` instead of importing service modules directly.

## Requirements

- Python **3.9+** (minimal MCP transport built-in)
- Python **3.10+** recommended for official `mcp` SDK (`pip install mcp`)
- Backend dependencies installed (`pip install -r backend/requirements.txt`)
- `backend/.env` with `SEC_EDGAR_USER_AGENT`

## Install

```bash
cd quantpilot/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional (Python 3.10+): pip install "mcp>=1.0.0"
```

## Run the MCP server (Terminal 1)

From the **quantpilot** project root:

```bash
cd /path/to/quantpilot
source backend/.venv/bin/activate
python mcp_server/server.py
```

The server listens on **stdio** (no HTTP port). Leave this terminal open.

## Run FastAPI (Terminal 2)

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

**Important:** Start the MCP server **before** running analysis, or ensure the backend can spawn it (auto-spawn via `mcp_client.py`).

## Available tools

| Tool | Input | Output |
|------|-------|--------|
| `get_stock_price` | `ticker` | current price, previous close, change %, volume |
| `get_stock_fundamentals` | `ticker` | market cap, P/E, EPS, dividend yield, 52w range, sector, industry |
| `get_stock_news` | `ticker`, `limit` (default 5) | headlines with publisher, link, published time |
| `get_price_history` | `ticker`, `period` (default `1mo`) | OHLCV rows |
| `get_recent_filings` | `ticker`, `limit` (default 3) | SEC filings with type, date, URL |
| `get_company_facts` | `ticker` | CIK, company name, SIC, state |
| `get_ir_materials` | `ticker`, `max_pages` | Investor-relations pages via allowlisted httpx (optional OpenClaw IR fallback) |
| `fetch_browser_page` | `url` | Single allowlisted IR/public page excerpt |
| `get_shareholder_letter` | `ticker` | Best-effort shareholder / annual letter excerpt |
| `snapshot_active_browser_tab` | *(none)* | Reads the active tab of the user's own signed-in browser via OpenClaw's existing-session attach — used for authenticated pages (e.g. brokerage positions) we must never fetch ourselves |

## Browser MCP + OpenClaw (PRD v3 Phase 10 / repo Phase 11)

IR tools use allowlisted **httpx** first. Set `OPENCLAW_IR_FALLBACK=true` (plus `OPENCLAW_BROWSER_URL`) to try OpenClaw navigate when httpx fails or returns thin content. Set `BROWSER_MCP_ENABLED=false` to disable. Investigations escalate to `get_ir_materials` / `get_shareholder_letter` when news/filings are thin.

Agents `news_agent`, `financial_metrics_agent`, `sec_filing_agent`, and `earnings_agent` (IR) use MCP tools.

## Phase 11.1 — Portfolio Auto-Sync

`snapshot_active_browser_tab` is the backbone of the Portfolio Sync feature (`POST /api/portfolio/sync/preview`): the user opens their broker's positions page in their own already-signed-in Chrome, and OpenClaw's existing-session driver reads the visible text — no credentials or login flow ever touch QuantPilot. If OpenClaw isn't configured, the same endpoint accepts pasted page text instead, so the feature works with zero extra setup. Either way, a broker-agnostic LLM extraction step (`app/agents/portfolio_sync_agent.py`, with a regex fallback when no OpenAI key is set) turns raw text into `{ticker, shares, avg_cost}` rows for the user to review before saving.

## Test tools directly

```bash
cd quantpilot
source backend/.venv/bin/activate
python -c "
from mcp_server.tools.stock_tools import get_stock_price
print(get_stock_price('AAPL'))
"
```

## Test via analysis API

```bash
curl -X POST http://localhost:8000/api/analysis/AAPL
```

## How agents connect

```python
from app.mcp_client import call_tool

data = await call_tool("get_stock_fundamentals", {"ticker": "AAPL"})
```

If the MCP server is unavailable, agents return graceful fallback messages.
