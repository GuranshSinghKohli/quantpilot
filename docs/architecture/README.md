# Architecture — Phase 1

## System overview

```
┌─────────────────┐     HTTP (fetch)      ┌─────────────────┐
│  Next.js UI     │ ────────────────────► │  FastAPI        │
│  localhost:3000 │                       │  localhost:8000 │
└─────────────────┘                       └────────┬────────┘
                                                   │
                          ┌────────────────────────┼────────────────────────┐
                          ▼                        ▼                        ▼
                 ┌────────────────┐      ┌────────────────┐      ┌────────────────┐
                 │ yahoo_finance  │      │ sec_edgar      │      │ /health        │
                 │ (yfinance)     │      │ (data.sec.gov) │      │                │
                 └────────────────┘      └────────────────┘      └────────────────┘
```

## API boundaries

| Endpoint | Service | External source |
|----------|---------|-----------------|
| `GET /api/stocks/{ticker}` | `yahoo_finance.py` | Yahoo Finance via yfinance |
| `GET /api/filings/{ticker}` | `sec_edgar.py` | SEC EDGAR (`data.sec.gov`) |
| `GET /health` | `main.py` | None |

## Data flow (search)

1. User enters ticker in `StockSearch` component.
2. Frontend calls `/api/stocks/{ticker}` and `/api/filings/{ticker}` in parallel.
3. FastAPI routers delegate to service layer.
4. Services fetch external data, validate with Pydantic, return JSON.
5. Frontend renders stock metrics and 3 most recent filings.

## Out of scope (Phase 1)

- AI agents, LangGraph, CrewAI
- Vector DB, Redis, memory
- Authentication
