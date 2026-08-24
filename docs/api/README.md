# QuantPilot API

Interactive OpenAPI lives on the running backend at `/docs` (Swagger) and `/redoc`.
Schemas are the FastAPI / Pydantic models — this page is an index, not a second source of truth.

## Authentication

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | Email + password (min 8). Returns JWT. |
| POST | `/api/auth/login` | Same. |
| GET | `/api/auth/me` | Bearer required. |

Send `Authorization: Bearer <token>`. Guests may send `X-Guest-Id` instead for ledger/history scoping. Expired JWTs return 401; the frontend clears the stored token.

## Portfolio & watchlist

| Method | Path |
|---|---|
| GET/POST/DELETE | `/api/watchlist` |
| GET | `/api/portfolio` |
| POST | `/api/portfolio/sync` |

Scoped to the signed-in user (or the anonymous local user). User A cannot read User B.

## Investigations & evidence

| Method | Path |
|---|---|
| POST | `/api/investigations` |
| POST | `/api/investigations/investigate` |
| GET | `/api/investigations` |
| GET | `/api/investigations/{id}` |
| POST | `/api/investigations/{id}/run` |
| GET | `/api/investigations/search` |
| POST | `/api/investigations/{id}/evidence` |

`GET /api/investigations/{id}` is owner-scoped: another user receives **404**.

## Company intelligence & dashboard

| Method | Path |
|---|---|
| GET | `/api/dashboard` |
| GET | `/api/companies/{ticker}/intelligence` |
| GET | `/api/investigations/companies/{ticker}/memory` |

## Alerts & monitoring

| Method | Path |
|---|---|
| GET/POST | `/api/alerts/rules` |
| GET | `/api/alerts/events` |
| POST | `/api/alerts/events/{id}/feedback` |
| POST | `/api/investigations/sweep` |
| GET | `/api/investigations/trigger/{ticker}` |

## Deep research

| Method | Path |
|---|---|
| POST | `/api/analysis/{ticker}` |
| POST | `/api/analysis/{ticker}/stream` |
| POST | `/api/chat/{ticker}` |

## Health

| Method | Path | Meaning |
|---|---|---|
| GET | `/health` | Process is up; includes cheap DB/Redis flags. |
| GET | `/ready` | Ready to take traffic (DB reachable, not draining). |

All JSON errors include `error`, `message`, `detail`, `timestamp`, and `request_id`.
