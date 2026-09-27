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

Read by `python -m app.pipeline …`. The API does not need any of them and starts without provider credentials. The lake is live as of Step 2.3, Kafka as of Step 2.4 (`docs/PHASE2_4_PLAN.md`), and the external source adapters as of Step 2.5 (`docs/PHASE2_5_PLAN.md`).

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
| `PIPELINE_RETRY_MAX_ATTEMPTS` / `PIPELINE_RETRY_BASE_SECONDS` / `PIPELINE_RETRY_MAX_SECONDS` | Optional | Bounded exponential backoff with full jitter (defaults `5` / `0.5` / `30`) | `5` |

### External sources (Step 2.5)

"Required when enabled" means `python -m app.pipeline ingest <domain>` (and `make ingest-<domain>`) refuses to start without it and names the variable; nothing else needs it. Keys are secrets: they are sent only in the `Authorization` header and never logged. Put them in the git-ignored `backend/.env` for host runs, or the git-ignored repo-root `.env` / your shell for `make ingest-*` (compose interpolation). Never commit them.

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `NEWS_API_KEY` | Required when news is ingested | Massive API key (free Stocks Basic plan is enough) | — |
| `MARKET_API_KEY` | Required when market data is ingested | Massive API key; may be the same key as `NEWS_API_KEY` | — |
| `SEC_EDGAR_USER_AGENT` | Required when companies are ingested (and for the API's SEC tools) | Must contain a real contact email (SEC fair-access policy); `example.com` is rejected by the ingestor | `QuantPilot research you@yourdomain.com` |
| `NEWS_PROVIDER` / `MARKET_PROVIDER` / `COMPANY_PROVIDER` | Optional | Registry entry to use; only `massive` / `massive` / `sec` exist | `massive` |
| `NEWS_BASE_URL` / `MARKET_BASE_URL` / `COMPANY_BASE_URL` | Optional | Provider base URL (defaults `https://api.massive.com`, same, `https://www.sec.gov`). Must be https except loopback, `host.docker.internal` or a single-label compose host (local mocks) | `https://api.massive.com` |
| `NEWS_TICKERS` / `MARKET_SYMBOLS` / `COMPANY_SYMBOLS` | Optional | Default universe per domain (all default `AAPL,MSFT,NVDA`; validated symbols). Empty `NEWS_TICKERS` = market-wide news | `AAPL,MSFT` |
| `NEWS_REQUESTS_PER_MINUTE` / `MARKET_REQUESTS_PER_MINUTE` / `COMPANY_REQUESTS_PER_MINUTE` | Optional | Client-side throttle (defaults `5` = Massive free plan, `5`, `600` = SEC's 10/s) | `5` |
| `SOURCE_TIMEOUT_SECONDS` | Optional | Per-request timeout (default `20`) | `20` |
| `SOURCE_MAX_RETRY_AFTER_SECONDS` | Optional | Longest HTTP 429 `Retry-After` the ingestor will wait; longer stops the run (default `120`) | `120` |
| `INGEST_LOOKBACK_DAYS` | Optional | Window when no start is given (default `7`) | `7` |
| `INGEST_MAX_WINDOW_DAYS` | Optional | Largest allowed window (default `366`) | `366` |
| `INGEST_MAX_RECORDS` / `INGEST_PAGE_SIZE` / `INGEST_MAX_PAGES` | Optional | Per-run record cap across all streams (default `1000`, max `50000`), page size (default `100`, max `1000`), pages per stream (default `20`) | `1000` |

`make ingest-*` also accepts `NEWS_START`, `NEWS_END`, `MARKET_START`, `MARKET_END` (`YYYY-MM-DD` or ISO 8601 with offset), `COMPANY_ALL=1`, `MAX_RECORDS`, `PAGE_SIZE` and `MAX_PAGES`, passed as CLI flags.

| Environment | What to set |
|---|---|
| Local development | `SEC_EDGAR_USER_AGENT` for `make ingest-company`; a free Massive key in `NEWS_API_KEY`/`MARKET_API_KEY` for news/market. Everything else has defaults |
| Production (target) | The same keys from a secret manager (not `.env`), `https` base URLs (enforced), throttle values matching the paid plan |

### Spark processing (Step 2.6)

Read only by `python -m app.pipeline process` (the `spark` compose service / `make spark-*`); the API never reads them. S3A takes credentials from the standard `AWS_*` variables (MinIO's in compose); they are never put into the Spark configuration.

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `SPARK_MASTER` | Optional | `local[*]` (default), `local[N]`, `local`; `spark://`, `k8s://`, `yarn` are accepted but untested | `local[*]` |
| `SPARK_APP_NAME` | Optional | Spark application name (default `quantpilot-processing`) | `quantpilot-processing` |
| `SPARK_SHUFFLE_PARTITIONS` | Optional | `spark.sql.shuffle.partitions` (default `8`, 1–2000) | `8` |
| `SPARK_LOG_LEVEL` | Optional | `ERROR`, `WARN` (default), `INFO`, `DEBUG` | `WARN` |
| `SPARK_DRIVER_MEMORY` | Optional | Driver memory, e.g. `2g` (default) | `2g` |
| `SPARK_MAX_RECORDS_PER_FILE` | Optional | Split a partition's Parquet file past this many rows (default `1000000`) | `1000000` |
| `PROCESS_MAX_WINDOW_DAYS` | Optional | Largest `--start/--end` window (default `366`) | `366` |
| `PROCESS_MAX_INPUT_OBJECTS` | Optional | Refuse a job selecting more raw objects (default `100000`) | `100000` |
| `CODE_VERSION` | Optional | Recorded on `processing_jobs` and manifests; `make spark-*` sets the git short SHA | `e80ae48` |

`make spark-*` also accepts `PROCESS_START`, `PROCESS_END` (raw ingestion dates, `YYYY-MM-DD`), `PROCESS_PREFIX`, `PROCESS_OBJECTS` (comma-separated raw keys) and `PROCESS_OUTPUT_PREFIX`, passed as CLI flags.

Canonical loading (`make load-news|load-market|load-company|load-all`) adds no environment variables. It reads the same `DATABASE_URL` and lake settings (`LAKE_*`, `AWS_*`), and the `load` compose service gets no provider keys and no Kafka settings. By default it loads the latest `COMPLETED` processing job per domain. Make variables passed as CLI flags override this:
- `LOAD_JOB=<processing job id>`, or `LOAD_MANIFEST=<manifest key or URI>`;
- `LOAD_SINCE` / `LOAD_UNTIL` (record partition dates, `YYYY-MM-DD`);
- `LOAD_BATCH_SIZE` (default `1000`).

`--allow-synthetic` exists for tests and is refused unless the database name contains `test`.

Pipeline hardening (Step 2.8) adds no required variables. Optional:

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `PIPELINE_STALE_RUN_SECONDS` | Optional | Ingestion run `RUNNING` longer than this is reported by `make pipeline-check` (never auto-failed) | `3600` |
| `PIPELINE_STALE_JOB_SECONDS` | Optional | Same for processing jobs | `3600` |
| `PIPELINE_STALE_LOAD_SECONDS` | Optional | Same for `CANONICAL_LOAD_RUNNING` | `3600` |

`GET /metrics` needs no config. `make metrics-up` starts an optional Prometheus (`compose --profile observability`) that scrapes the API. The API starts if Prometheus is down.

Test-only: `TEST_KAFKA_BOOTSTRAP` enables the `kafka` tier (`make test-kafka`; per-test `test.<id>.*` topics and groups, deleted afterwards). `TEST_S3_ENDPOINT`, `TEST_S3_ACCESS_KEY_ID`, `TEST_S3_SECRET_ACCESS_KEY` enable the `s3` tier (`make test-s3`), which creates and deletes its own `quantpilot-test-*` buckets. The test suite always pins the lake to `backend/tests/_test_lake/`, Kafka to the unroutable `127.0.0.1:9`, and topic/group names to `test.unconfigured.*`. It also blanks `NEWS_API_KEY`/`MARKET_API_KEY` and points every source base URL at `http://127.0.0.1:9`, so no test can reach a real provider; adapter tests use an httpx `MockTransport`. `make test-kafka` also sets the `TEST_S3_*` variables so the mocked-provider end-to-end tests run against MinIO. The `spark` tier needs pyspark + Java 17 and runs in the `spark-test` image (`make test-spark`, which sets `TEST_S3_*` for MinIO); `SPARK_TEST_DATABASE_URL` / `SPARK_TEST_KAFKA_BOOTSTRAP` pass `TEST_DATABASE_URL` / `TEST_KAFKA_BOOTSTRAP` into that container to run every tier there.

## Frontend (Vercel / local)

| Variable | Required / Optional | Description | Example value |
|----------|-------------------|-------------|---------------|
| `NEXT_PUBLIC_API_URL` | **Required** | Full URL of the deployed FastAPI backend (no trailing slash) | `https://quantpilot-backend.up.railway.app` |

Copy `frontend/.env.local.example` to `frontend/.env.local` for local development.

## Notes

- After deploying to Vercel, add your Vercel URL to backend `ALLOWED_ORIGINS` on Railway and redeploy the backend.
- `NEXT_PUBLIC_*` variables are embedded in the client bundle at build time — redeploy Vercel after changing them.
- For Phase 7 on Railway: add a **PostgreSQL** plugin, copy `DATABASE_URL` into the API service, and set `JWT_SECRET_KEY`. Watchlists and portfolios then survive redeploys (JSON file storage is retired).
