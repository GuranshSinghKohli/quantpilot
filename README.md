# QuantPilot AI

Phase 1 — Foundation & Architecture. FastAPI backend + Next.js frontend for stock and SEC filing lookups.

**No AI agents in this phase** — infrastructure and financial APIs only.

## Stack

| Layer    | Tech                          |
|----------|-------------------------------|
| Backend  | FastAPI, yfinance, httpx      |
| Frontend | Next.js 14, Tailwind CSS      |
| Data     | Yahoo Finance, SEC EDGAR      |

## Project structure

```
quantpilot/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── routers/       # stocks, filings
│   │   ├── services/      # yahoo_finance, sec_edgar
│   │   └── models/        # Pydantic schemas
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/page.tsx
│   └── components/StockSearch.tsx
└── README.md
```

## Quick start

### 1. Backend (port 8000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set SEC_EDGAR_USER_AGENT with your email (SEC requirement)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Frontend (port 3000)

Requires [Node.js](https://nodejs.org/) 18+.

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open http://localhost:3000 and search a ticker (e.g. `AAPL`). Results show stock metrics and the 3 most recent SEC filings.

## API endpoints

| Method | Path                    | Description                    |
|--------|-------------------------|--------------------------------|
| GET    | `/health`               | `{"status": "ok"}`             |
| GET    | `/api/stocks/{ticker}`  | Yahoo Finance stock data       |
| GET    | `/api/filings/{ticker}` | 3 most recent SEC EDGAR filings |

Interactive docs: http://localhost:8000/docs

## Environment variables

**Backend** (`backend/.env`):

- `SEC_EDGAR_USER_AGENT` — Required format: `AppName/1.0 (your@email.com)`

**Frontend** (`frontend/.env.local`):

- `NEXT_PUBLIC_API_URL` — Default: `http://localhost:8000`

## Git (local)

```bash
git add .
git commit -m "Phase 1: FastAPI + Next.js foundation"
```

Push to GitHub later with `git remote add origin ...` and `git push -u origin main`.
