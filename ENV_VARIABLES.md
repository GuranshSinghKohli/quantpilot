# Environment Variables

All secrets belong in `.env` / `.env.local` files — never commit them to Git.

## Backend (Railway / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `OPENAI_API_KEY` | **Required** | Powers all LangGraph agents and ChromaDB embeddings (`text-embedding-3-small`) | `sk-proj-...` |
| `SEC_EDGAR_USER_AGENT` | **Required** | SEC EDGAR API requires a descriptive User-Agent with contact info | `QuantPilot/1.0 (you@email.com)` |
| `ALLOWED_ORIGINS` | **Required** (production) | Comma-separated CORS origins allowed to call the API | `https://quantpilot.vercel.app,http://localhost:3000` |
| `DATABASE_URL` | **Required** (production) | PostgreSQL connection string. Locally defaults to SQLite at `backend/data/quantpilot.db` if unset. Railway Postgres plugin sets this automatically. | `postgresql://user:pass@host:5432/railway` |
| `JWT_SECRET_KEY` | **Required** (production) | Secret used to sign auth tokens. Change from the dev default before deploying. | long random string |
| `JWT_EXPIRE_DAYS` | Optional | Access token lifetime in days (default `7`) | `7` |
| `INVESTIGATION_NOISE_PCT` | Optional | Absolute daily move % below which proactive runs may skip as market noise (default `1.5`). User-initiated investigate ignores this unless `skip_if_noise=true`. | `1.5` |
| `INVESTIGATIONS_SWEEP_ENABLED` | Optional | APScheduler portfolio investigation sweep (PRD v3 · 11). Default `false`. | `false` |
| `INVESTIGATION_SWEEP_INTERVAL_MINUTES` | Optional | Minutes between investigation sweeps (default `30`, clamped 5–240) | `30` |
| `INVESTIGATION_SWEEP_MAX_PER_RUN` | Optional | Max investigations launched per sweep (default `5`) | `5` |
| `INVESTIGATION_COOLDOWN_HOURS` | Optional | Skip re-investigate if a case exists within this many hours (default `6`) | `6` |
| `INVESTIGATION_VOL_Z_THRESHOLD` | Optional | Min \|move / realized vol\| to investigate when vol is known (default `1.5`) | `1.5` |
| `INVESTIGATION_IDIO_PCT` | Optional | Min \|ticker − SPY\| residual % to treat as idiosyncratic (default `1.0`) | `1.0` |
| `INVESTIGATION_BENCHMARK` | Optional | Benchmark ticker for residual gate (default `SPY`) | `SPY` |
| `INVESTIGATION_NOTIFY_ENABLED` | Optional | Create in-app notifications when proactive investigations clear the materiality bar (default `true`) | `true` |
| `INVESTIGATION_NOTIFY_MIN_SCORE` | Optional | Materiality score (0–100) required to notify (default `45`) | `45` |
| `INVESTIGATION_NOTIFY_MIN_Z` | Optional | Alternate notify gate: abs move z-score (default `2.0`) | `2.0` |
| `INVESTIGATION_NOTIFY_MIN_DEPTH` | Optional | Alternate notify gate: min investigation depth `light`/`standard`/`deep` (default `standard`) | `standard` |
| `INVESTIGATION_NOTIFY_COOLDOWN_HOURS` | Optional | Suppress duplicate investigation notifies per ticker (default `6`) | `6` |
| `ALERT_EMAIL_ENABLED` | Optional | Log intent to email material alerts (SMTP not wired yet; in-app is primary) | `false` |
| `BRIEFING_ENABLED` | Optional | Run APScheduler daily portfolio briefings (`true`/`false`, default `true`) | `true` |
| `BRIEFING_HOUR_UTC` | Optional | UTC hour for daily briefing job (default `12`) | `12` |
| `BRIEFING_MINUTE_UTC` | Optional | UTC minute for daily briefing job (default `0`) | `0` |
| `BRIEFING_MAX_HOLDINGS` | Optional | Cap holdings included in one briefing (default `12`) | `12` |
| `REDIS_URL` | Optional | Redis connection URL for cache/queues. If unset/unreachable, uses in-memory fallback. | `redis://localhost:6379/0` |
| `ALERTS_ENABLED` | Optional | Run smart-alert evaluator on an interval (default `true`) | `true` |
| `ALERT_INTERVAL_MINUTES` | Optional | Minutes between alert evaluation jobs (default `15`) | `15` |
| `QUOTE_CACHE_TTL_SECONDS` | Optional | Quote cache TTL in Redis/memory (default `60`) | `60` |
| `BROWSER_MCP_ENABLED` | Optional | Enable IR/browser MCP tools used by investigations + earnings (default `true`) | `true` |
| `MCP_BROWSER_TIMEOUT_SECONDS` | Optional | Timeout for IR/browser MCP tools (default `45`) | `45` |
| `OPENCLAW_IR_FALLBACK` | Optional | If `true`, try OpenClaw navigate when httpx IR fetch fails/thin. Default `false` (httpx primary; OpenClaw navigate can be flaky / open tabs). Existing-session snapshots for portfolio sync are separate. | `false` |
| `OPENCLAW_BROWSER_URL` | Optional | OpenClaw browser control base URL (preferred IR path) | `http://127.0.0.1:18791` |
| `OPENCLAW_GATEWAY_TOKEN` | Optional | Bearer token for OpenClaw browser/gateway auth | gateway token |
| `OPENCLAW_GATEWAY_PASSWORD` | Optional | Alternate OpenClaw password auth header | password |
| `OPENCLAW_BROWSER_PROFILE` | Optional | OpenClaw browser profile name (default `openclaw`) | `openclaw` |
| `OPENCLAW_BROWSER_PROFILE_EXISTING` | Optional | OpenClaw profile for the "existing session" attach used by Portfolio Sync (default `user`) | `user` |
| `OPENCLAW_USE_CLI` | Optional | Also try `openclaw browser` CLI for fetches. Leave off unless the CLI is verified: each invocation costs ~8s of Node startup, and `snapshot` may attach to a blank tab instead of the one `open` created, so IR fetches can exhaust the browser timeout and return nothing. The allowlisted httpx fallback is faster and covers the same IR pages. | `false` |
| `LANGSMITH_TRACING` | Optional | Enable LangSmith / LangChain tracing for LangGraph runs (default `false`) | `true` |
| `LANGSMITH_API_KEY` | Optional | LangSmith API key (`LANGCHAIN_API_KEY` also accepted) | `lsv2_...` |
| `LANGSMITH_PROJECT` | Optional | LangSmith project name (default `quantpilot`) | `quantpilot` |
| `SENTRY_DSN` | Optional | Sentry DSN for API error reporting | `https://...@o....ingest.sentry.io/...` |
| `SENTRY_ENVIRONMENT` | Optional | Sentry environment tag (default `development` / `ENVIRONMENT`) | `production` |
| `SENTRY_TRACES_SAMPLE_RATE` | Optional | Sentry performance sample rate (default `0.1`) | `0.1` |
| `OTEL_ENABLED` | Optional | Enable OpenTelemetry without an exporter endpoint (default `false`) | `true` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional | OTLP HTTP traces endpoint | `http://localhost:4318/v1/traces` |
| `OTEL_SERVICE_NAME` | Optional | Service name for OTel resource (default `quantpilot-api`) | `quantpilot-api` |
| `QUANTPILOT_ROOT` | Optional | Override monorepo root path if MCP server is not auto-detected | `/app` |
| `PORT` | Optional | Injected by Railway; used by uvicorn | `8000` |

Copy `backend/.env.example` to `backend/.env` for local development.

## Frontend (Vercel / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `NEXT_PUBLIC_API_URL` | **Required** | Full URL of the deployed FastAPI backend (no trailing slash) | `https://quantpilot-backend.up.railway.app` |

Copy `frontend/.env.local.example` to `frontend/.env.local` for local development.

## Notes

- After deploying to Vercel, add your Vercel URL to backend `ALLOWED_ORIGINS` on Railway and redeploy the backend.
- `NEXT_PUBLIC_*` variables are embedded in the client bundle at build time — redeploy Vercel after changing them.
- For Phase 7 on Railway: add a **PostgreSQL** plugin, copy `DATABASE_URL` into the API service, and set `JWT_SECRET_KEY`. Watchlists and portfolios then survive redeploys (JSON file storage is retired).
