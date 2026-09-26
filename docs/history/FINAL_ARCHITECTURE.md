# QuantPilot — final architecture

This is the architecture as implemented after Phases 1–12. Where older docs disagree, this file and the code win.

## Runtime map

```
Browser (Next.js 14)
        │  REST + JWT / X-Guest-Id
        ▼
FastAPI  ── Auth (bcrypt + JWT) ── owner_key scope
        │
        ├── Investigation runner (detect → plan → MCP collect → rank → verify → DA → persist)
        │         └── optional LangGraph investigation graph
        ├── Deep-research LangGraph (analysis + investment memo)
        ├── MCP stdio tools ── yfinance / SEC EDGAR / allowlisted IR httpx
        │         └── Browser MCP ── OpenClaw (opt-in, targeted research only)
        ├── Company memory + Evidence Ledger (Postgres / SQLite)
        ├── ChromaDB (report + optional ledger search)
        └── Redis (optional) ── quote cache, job queues, sweep lock (TTL)
                    └── APScheduler: briefings / alerts / investigation sweep
```

There are **two pipelines**. They stay separate:

| Pipeline | Entry | Orchestration | Output |
|---|---|---|---|
| Investigate | `POST /api/investigations/investigate` | `investigation_runner.py` | Evidence Ledger case |
| Deep research | `POST /api/analysis/{ticker}` | LangGraph `analysis_graph.py` | Report + memo |

## Data stores

| Store | Role | Failure mode |
|---|---|---|
| PostgreSQL (SQLite locally) | Users, portfolio, watchlist, investigations, claims, evidence, company memory, alerts | `/ready` fails if unreachable |
| Redis | Cache, queues, sweep lock | In-memory fallback; locks expire (`SWEEP_LOCK_TTL_SECONDS`) |
| ChromaDB | Embeddings for report/ledger search | Optional; keyword search still works |

## External services & secrets

| Service | Secret / config | Required in production |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | Yes (generation + embeddings) |
| SEC EDGAR | `SEC_EDGAR_USER_AGENT` | Yes |
| JWT | `JWT_SECRET_KEY` | Yes |
| Postgres | `DATABASE_URL` | Yes |
| Redis | `REDIS_URL` | Optional |
| OpenClaw | `OPENCLAW_GATEWAY_TOKEN` / password | Optional; never given DB or brokerage secrets |
| LangSmith / Sentry / OTel | optional keys | No |

OpenClaw receives only browser-control configuration. It does not receive `DATABASE_URL`, `JWT_SECRET_KEY`, or brokerage credentials. Webpage text is sanitized as untrusted data.

## Background jobs

| Job | Trigger | Idempotency |
|---|---|---|
| Daily briefing | APScheduler cron | One job id, `max_instances=1`, `coalesce=True` |
| Alert evaluation | Interval | Rule cooldown + `last_triggered_at` |
| Investigation sweep | Interval / Redis queue | Owner+ticker cooldown, event fingerprint, sweep lock with TTL |
| Material notify | After proactive investigation | Per-user/ticker notify cooldown |

A crashed sweep holder cannot block monitoring forever: the lock expires.

## Failure boundaries

- MCP / OpenClaw timeouts fail the tool, not the process.
- Investigation exceptions mark the case `failed` and return the row.
- Scheduler jobs log and continue; a ticker error does not abort the sweep.
- Shutdown sets a draining flag, stops the scheduler, releases the sweep lock, closes Redis, and disposes the DB engine.

## Correlation

`X-Request-Id` (generated if absent) → `investigation_id` on runner logs → evidence/claim ids in the ledger. Follow `request_id` in JSON logs to answer “why did this investigation fail?”
