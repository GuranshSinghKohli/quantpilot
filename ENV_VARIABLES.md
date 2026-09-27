# Environment Variables

All secrets belong in `.env` / `.env.local` files — never commit them to Git.

## Backend (Railway / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `OPENAI_API_KEY` | **Required** | Powers all LangGraph agents and ChromaDB embeddings (`text-embedding-3-small`) | `sk-proj-...` |
| `SEC_EDGAR_USER_AGENT` | **Required** | SEC EDGAR API requires a descriptive User-Agent with contact info | `QuantPilot/1.0 (you@email.com)` |
| `ALLOWED_ORIGINS` | **Required** (production) | Comma-separated CORS origins allowed to call the API | `https://quantpilot.vercel.app,http://localhost:3000` |
| `DATABASE_URL` | **Required** (production) | PostgreSQL connection string. Locally defaults to SQLite at `backend/data/quantpilot.db` if unset. Railway Postgres plugin sets this automatically. Schema is managed by Alembic (`alembic upgrade head`). | `postgresql://user:pass@host:5432/railway` |
| `DB_AUTO_MIGRATE` | Optional | Run `alembic upgrade head` at API startup. Unset = on only when `ENVIRONMENT=development`. When off and the database is behind, the scheduler stays down and `/ready` reports `migrations_current: false`. | `false` |
| `ENVIRONMENT` | Optional | `development` (default) or `production`. Production refuses the dev `JWT_SECRET_KEY` and logs to stdout only. | `production` |
| `SERVICE_NAME` / `SERVICE_VERSION` | Optional | Stamped on every JSON log line and `/health` (defaults `quantpilot-api` / `0.1.0`) | `quantpilot-api` |
| `JWT_SECRET_KEY` | **Required** (production) | Secret used to sign auth tokens. Change from the dev default before deploying. | long random string |
| `JWT_EXPIRE_DAYS` | Optional | Access token lifetime in days (default `7`) | `7` |
| `INVESTIGATION_NOISE_PCT` | Optional | Absolute daily move % below which proactive runs may skip as market noise (default `1.5`). User-initiated investigate ignores this unless `skip_if_noise=true`. | `1.5` |
| `INVESTIGATIONS_SWEEP_ENABLED` | Optional | APScheduler holdings investigation sweep (default `false`) | `false` |
| `INVESTIGATION_REOPEN_MOVE_DELTA` | Optional | Reopen a cooled-down ticker if the new move is this many percentage points larger (default `3.0`) | `3.0` |
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
| `REDIS_URL` | Optional | Redis connection URL for cache/queues. If unset/unreachable, uses in-memory fallback. | `redis://localhost:6379/0` |
| `QUOTE_CACHE_TTL_SECONDS` | Optional | Quote cache TTL in Redis/memory (default `60`) | `60` |
| `BROWSER_MCP_ENABLED` | Optional | Enable IR/browser MCP tools used by investigations + earnings (default `true`) | `true` |
| `MCP_BROWSER_TIMEOUT_SECONDS` | Optional | Timeout for IR/browser MCP tools (default `45`) | `45` |
| `MAX_BROWSER_TASKS_PER_INVESTIGATION` | Optional | Cap expensive Browser MCP contract tools per case (default `2`) | `2` |
| `MAX_BROWSER_PAGES_PER_TASK` | Optional | Max official pages visited per document-discovery task (default `4`) | `4` |
| `MAX_BROWSER_RETRIES` | Optional | Retries for transient browser connection/load failures only (default `1`) | `1` |
| `BROWSER_NAV_TIMEOUT_SECONDS` | Optional | OpenClaw navigation timeout (default `20`) | `20` |
| `BROWSER_DISCOVERY_TIMEOUT_SECONDS` | Optional | Document discovery budget (default `30`) | `30` |
| `BROWSER_EXTRACT_TIMEOUT_SECONDS` | Optional | PDF/page extraction timeout (default `15`) | `15` |
| `OPENCLAW_IR_FALLBACK` | Optional | If `true`, try OpenClaw navigate when httpx IR fetch fails/thin. Default `false` (httpx primary; OpenClaw navigate can be flaky / open tabs). | `false` |
| `OPENCLAW_BROWSER_URL` | Optional | OpenClaw browser control base URL (preferred IR path) | `http://127.0.0.1:18791` |
| `OPENCLAW_GATEWAY_TOKEN` | Optional | Bearer token for OpenClaw browser/gateway auth | gateway token |
| `OPENCLAW_GATEWAY_PASSWORD` | Optional | Alternate OpenClaw password auth header | password |
| `OPENCLAW_BROWSER_PROFILE` | Optional | OpenClaw browser profile name (default `openclaw`) | `openclaw` |
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
| `ENVIRONMENT` | Optional | `development` or `production`. Production hides internal error detail. | `production` |
| `RATE_LIMIT_ENABLED` | Optional | Rate-limit expensive investigate/analysis routes (default `true`) | `true` |
| `RATE_LIMIT_EXPENSIVE_PER_MINUTE` | Optional | Max expensive POSTs per IP per minute (default `8`) | `8` |
| `MAX_REQUEST_BODY_BYTES` | Optional | Reject larger JSON bodies (default `1000000`) | `1000000` |
| `MAX_LLM_CALLS_PER_INVESTIGATION` | Optional | Hard cap on LLM calls for one investigation (default `24`) | `24` |
| `MAX_TOKENS_PER_INVESTIGATION` | Optional | Token budget for one investigation (default `80000`) | `80000` |
| `MAX_RETRIES` | Optional | LLM retry attempts after the first call (default `1`) | `1` |
| `SWEEP_LOCK_TTL_SECONDS` | Optional | Monitoring sweep lock TTL so a crash cannot block forever (default `180`) | `180` |

Copy `backend/.env.example` to `backend/.env` for local development.

## Ingestion pipeline (Phase 2, in progress)

Read by `python -m app.pipeline …`. The API does not need them. The lake is live as of Step 2.3 and Kafka as of Step 2.4 (see `docs/PHASE2_4_PLAN.md`).

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `LAKE_BACKEND` | Optional | `s3` (default; AWS S3 or MinIO) or `local` (filesystem, same key layout) | `s3` |
| `LAKE_BUCKET` | Optional | Lake bucket, validated against S3 naming rules (default `quantpilot-lake`) | `quantpilot-lake` |
| `LAKE_ENDPOINT_URL` | Optional | S3 endpoint. Empty = AWS; set for MinIO | `http://localhost:9000` |
| `LAKE_REGION` | Optional | Bucket region (default `us-east-1`) | `us-east-1` |
| `LAKE_LOCAL_ROOT` | Optional | Root directory for `LAKE_BACKEND=local` (default `backend/data/lake`, git-ignored) | `/tmp/quantpilot-lake` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | For `s3` | Standard AWS credential chain (env, profile or instance role); QuantPilot never reads them into its own settings. `make lake-*` passes the MinIO dev placeholders | — |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | Optional | Compose MinIO credentials. Dev-only defaults `quantpilot` / `quantpilot-dev-only`; override for anything shared | — |
| `MINIO_PORT` / `MINIO_CONSOLE_PORT` | Optional | Host ports for MinIO API / console (defaults `9000` / `9001`) | `9000` |
| `KAFKA_BOOTSTRAP_SERVERS` | Optional | Broker list (default `localhost:9092`) | `localhost:9092` |
| `KAFKA_TOPIC_NEWS_RAW` / `KAFKA_TOPIC_MARKET_RAW` / `KAFKA_TOPIC_COMPANY_RAW` / `KAFKA_TOPIC_DLQ` | Optional | Topic names (defaults `quantpilot.{news,market,company}.raw`, `quantpilot.ingestion.dlq`); must be distinct | `quantpilot.news.raw` |
| `KAFKA_CONSUMER_GROUP_LAKE` | Optional | Lake-writer consumer group (default `quantpilot.lake-writer`) | `quantpilot.lake-writer` |
| `KAFKA_MAX_MESSAGE_BYTES` | Optional | Max encoded event size (default `1000000`) | `1000000` |
| `KAFKA_CLIENT_ID` | Optional | Client id on every connection (default `quantpilot`) | `quantpilot` |
| `KAFKA_SECURITY_PROTOCOL` | Optional | `PLAINTEXT` (default, local only), `SSL`, `SASL_PLAINTEXT`, `SASL_SSL` | `SASL_SSL` |
| `KAFKA_SASL_MECHANISM` / `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD` | For `SASL_*` | Required together with a SASL protocol; the password is a secret and is never logged | — |
| `KAFKA_REPLICATION_FACTOR` | Optional | Replication factor used by `kafka init` (default `1`, single local broker) | `3` |
| `KAFKA_RAW_RETENTION_HOURS` / `KAFKA_DLQ_RETENTION_HOURS` | Optional | Topic retention (defaults `168` / `720`) | `168` |
| `KAFKA_DELIVERY_TIMEOUT_MS` | Optional | Producer delivery timeout per attempt (default `30000`) | `30000` |
| `KAFKA_POLL_TIMEOUT_SECONDS` | Optional | Lake-writer poll timeout (default `1.0`) | `1.0` |
| `KAFKA_PORT` | Optional | Host port for the compose broker (default `9092`) | `9092` |
| `INGEST_SYMBOLS` | Optional | Symbols for market/news adapters (default `AAPL,MSFT,NVDA`) | `AAPL,MSFT` |
| `PIPELINE_RETRY_MAX_ATTEMPTS` / `PIPELINE_RETRY_BASE_SECONDS` / `PIPELINE_RETRY_MAX_SECONDS` | Optional | Bounded exponential backoff with full jitter (defaults `5` / `0.5` / `30`) | `5` |

Test-only: `TEST_KAFKA_BOOTSTRAP` enables the `kafka` tier (`make test-kafka`; per-test `test.<id>.*` topics and groups, deleted afterwards). `TEST_S3_ENDPOINT`, `TEST_S3_ACCESS_KEY_ID`, `TEST_S3_SECRET_ACCESS_KEY` enable the `s3` tier (`make test-s3`), which creates and deletes its own `quantpilot-test-*` buckets. The test suite always pins the lake to `backend/tests/_test_lake/`, Kafka to the unroutable `127.0.0.1:9`, and topic/group names to `test.unconfigured.*`.

## Frontend (Vercel / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `NEXT_PUBLIC_API_URL` | **Required** | Full URL of the deployed FastAPI backend (no trailing slash) | `https://quantpilot-backend.up.railway.app` |

Copy `frontend/.env.local.example` to `frontend/.env.local` for local development.

## Notes

- After deploying to Vercel, add your Vercel URL to backend `ALLOWED_ORIGINS` on Railway and redeploy the backend.
- `NEXT_PUBLIC_*` variables are embedded in the client bundle at build time — redeploy Vercel after changing them.
- For Phase 7 on Railway: add a **PostgreSQL** plugin, copy `DATABASE_URL` into the API service, and set `JWT_SECRET_KEY`. Watchlists and portfolios then survive redeploys (JSON file storage is retired).
