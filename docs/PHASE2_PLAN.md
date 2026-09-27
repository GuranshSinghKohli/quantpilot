# QuantPilot — Phase 2 Plan: Data Ingestion + Data Lake

Status: **Proposed. Awaiting approval before any implementation.**
Baseline: `2bd4238 feat: establish QuantPilot Phase 1 foundation` (Alembic head `0004`).

Phase 2 builds reliable ingestion, streaming, raw storage, batch processing and lineage. It does **not** build agents, the knowledge graph, RAG, research reports, or a frontend dashboard.

```
External sources (yfinance, SEC EDGAR)
      │  ingestion workers (Python CLI): fetch → typed record → envelope
      ▼
Kafka  quantpilot.{news,market,company}.raw        (at-least-once, keyed)
      │  lake-writer consumer: validate envelope + payload, dedup, land
      ├────────────► DLQ  quantpilot.ingestion.dlq  (malformed / retries exhausted)
      ▼
Raw lake  s3://<bucket>/raw/<domain>/year=/month=/day=/<event_id>.json   (immutable)
      │  PySpark job per domain: schema → normalize → dedup → partitioned write
      ▼
Processed lake  s3://<bucket>/processed/<domain>/year=/month=/day=/part-*.parquet
      │  loader: Parquet → INSERT … ON CONFLICT (newest wins)
      ▼
PostgreSQL  market_bars · news_articles(+sources) · companies(+entities)
            ingestion_sources · ingestion_runs · ingestion_records · processing_jobs
```

---

## 1. Current architecture relevant to Phase 2

| Area | What exists (measured at `2bd4238`) | Relevance |
|---|---|---|
| Backend layout | `backend/app/{core,domain,repositories,db,services,routers,jobs,workflows,agents,…}`; layer rules in `backend/app/README.md` | Pipeline code slots in as a new orchestration-layer package |
| Config | `app/core/settings.py`: one pydantic-settings `Settings`, `get_settings()` cached, `SecretStr` for secrets, `validate_for_environment()`; `app/config.py` shim for legacy modules | Pipeline settings get added here; nothing reads `os.getenv` directly |
| Logging | `app/core/structured_logging.py`: JSON lines with `service/env/version`; `log_event(logger, event, **fields)` | Every pipeline stage logs through this; metrics are structured log events |
| DB + migrations | `app/core/db.py` (`build_engine`, `SessionLocal`), Alembic `0001–0004`, portable types (`sa.Uuid`, JSON→JSONB on Postgres), enums as VARCHAR + named CHECK, `upgrade_to_head()` | Phase 2 tables arrive as `0005`; same conventions |
| Core schema | `sources` (url_hash UNIQUE, content_hash, **`raw_uri`**, metadata), `entities` + `companies` (cik UNIQUE, UNIQUE(ticker, exchange)), `evidence_records`, `events`, `market_event_windows`, … all empty | `sources.raw_uri` was reserved for exactly this. News and companies reuse these tables instead of duplicating them |
| Repositories | `insert_ignore()` (dialect-aware ON CONFLICT DO NOTHING), `upsert_source`, `get_or_create_company`; flush-never-commit, return `(row, created)` | Loader reuses these; new upserts follow the same contract |
| Identity | `app/domain/identity.py`: `normalize_url`, `url_hash`, `content_hash` (whitespace-insensitive sha256), `event_dedup_key` | Reused for news URL identity and content hashing |
| External data | `app/services/yahoo_finance.py` (yfinance quote + `Ticker.news`), `app/services/sec_edgar.py` (httpx, `SEC_EDGAR_USER_AGENT`, `company_tickers.json`, submissions), and the same logic duplicated in `mcp_server/tools/{stock,sec}_tools.py` for agents | Source adapters reuse the fetch patterns. The API/MCP paths stay untouched |
| Redis | `app/services/redis_store.py`: cache, locks, list-based investigation queue, memory fallback | **Not** used by the pipeline. Kafka carries ingestion traffic, and Postgres is the dedup source of truth |
| Scheduling | APScheduler investigation sweep (default off) | No scheduled ingestion in Phase 2; runs are CLI/Make-triggered. Airflow stays a later phase (ADR-011) |
| Docker | Compose: `pgvector/pgvector:pg16`, Redis, one-shot `migrate`, backend, frontend; `docker-compose.dev.yml` overlay; `POSTGRES_PORT`/`REDIS_PORT` overrides | Kafka and MinIO join behind a compose **profile** so `make up` is unchanged |
| Tests | Root `tests/conftest.py` pins a throwaway SQLite file before any app import (guard test `test_test_isolation.py`); `tests/unit/`; `tests/integration/` (Postgres via `TEST_DATABASE_URL`, schema reset per session, per-test SAVEPOINT rollback); CI runs both tiers + `alembic check` | New tiers (`kafka`, `s3`, `spark`, `pipeline`) follow the same skip-unless-configured pattern |
| Frontend | Next.js 14 + TS + Tailwind. `frontend/lib/stack.ts` lists current vs target stack | Only the stack copy changes (Kafka, MinIO/S3, Spark, Parquet move to "current" once real) |
| Host constraints | macOS x86_64, 32 GB RAM, 12 CPU, Docker Desktop (currently stopped), **no Java runtime installed** | PySpark needs a JVM, so a runtime decision is required (§13, §14) |

**The roadmap is being merged.** The Phase 1 roadmap split this work into roadmap Phase 2 (raw landing), Phase 3 (Kafka) and part of Phase 4 (Spark). This plan implements all three as the new Phase 2. **Airflow is not included**; ingestion and processing are triggered by CLI/Make. ADR-010's topic names (`raw.document.ingested`) are replaced by the `quantpilot.<domain>.raw` scheme below, and ADR-010 will be amended.

## 2. Reusable components

- `Settings` / `get_settings()`: all pipeline config (topics, bootstrap servers, bucket, retry policy).
- `log_event`: pipeline metrics as structured events (no metrics server in Phase 2).
- `build_engine` / `SessionLocal`, `insert_ignore`, the Alembic conventions, `_one_of` CHECK enums.
- `normalize_url`, `url_hash`, `content_hash` from `app/domain/identity.py`.
- `upsert_source` (news → `sources`) and `get_or_create_company` (company → `entities` + `companies`).
- The yfinance and SEC request patterns (User-Agent, timeouts) from `app/services/`.
- The test fixtures pattern (`pg_engine`, per-test rollback, skip-unless-configured markers).

Not reused: the Redis queue (wrong semantics for a durable log), APScheduler (Airflow later), and ChromaDB (unrelated).

## 3. Data ingestion architecture

**Source adapters** (`app/pipeline/sources/`) implement one small interface:

```python
class SourceAdapter(Protocol):
    key: str                     # "yfinance.market_daily", "yfinance.news", "sec.company_tickers"
    domain: Domain               # news | market | company
    def fetch(self, request: FetchRequest) -> Iterator[RawItem]: ...   # original payload + external_id
    def to_record(self, item: RawItem) -> NewsRecord | MarketRecord | CompanyRecord: ...
```

Phase 2 sources are **real and keyless**:

| Adapter | Source | External ID | Volume per run |
|---|---|---|---|
| `yfinance.market_daily` | `yf.Ticker(s).history(interval="1d", auto_adjust=False)` for `INGEST_SYMBOLS` and a date range | `{symbol}:1d:{YYYY-MM-DD}` | symbols × trading days |
| `yfinance.news` | `yf.Ticker(s).news` (metadata + summary only; no article scraping) | provider item id (fallback: `url_hash`) | ~10 per symbol |
| `sec.company_tickers` | `https://www.sec.gov/files/company_tickers_exchange.json` (cik, name, ticker, exchange) | zero-padded CIK | ~10k rows, one request |

An RSS adapter is deliberately deferred. It would need an XML hardening dependency (`defusedxml`) and adds no new pipeline behaviour.

**The ingestion worker** (`python -m app.pipeline ingest --source <key> [--symbols …] [--start/--end]`):
1. Upserts the `ingestion_sources` row and creates an `ingestion_runs` row (`RUNNING`).
2. Fetches with bounded retries (§11) and converts each item to a typed record (§6). Items that fail conversion are counted `invalid` and sent to the DLQ with `component="ingestor"`.
3. Wraps each record in an `IngestionEvent` with a **deterministic** `event_id` and `dedup_key`.
4. Checks `ingestion_records` for the event. If it's already published or later, it counts a `duplicate` and skips; otherwise it inserts a `pending` row.
5. Publishes to Kafka (idempotent producer, `acks=all`), keyed by `dedup_key`. The delivery callback marks rows `published`; failures after the client's retries mark them `publish_failed`.
6. Closes the run with counters and a status (§8 table, §9 rules).

## 4. Kafka architecture

- **Broker (local):** single-node Apache Kafka in KRaft mode (`apache/kafka` image, no ZooKeeper), replication factor 1. A one-shot `kafka-init` service creates the topics idempotently (`--if-not-exists`).
- **Client:** `confluent-kafka` (librdkafka) is the one new Kafka dependency. It provides the idempotent producer, delivery callbacks, and manual offset commits.

| Topic (configurable via settings) | Key | Partitions | Retention | Producer | Consumer group |
|---|---|---|---|---|---|
| `quantpilot.news.raw` | `dedup_key` | 3 | 7 days | ingestion worker | `quantpilot.lake-writer` |
| `quantpilot.market.raw` | `dedup_key` | 3 | 7 days | ingestion worker | `quantpilot.lake-writer` |
| `quantpilot.company.raw` | `dedup_key` | 1 | 7 days | ingestion worker | `quantpilot.lake-writer` |
| `quantpilot.ingestion.dlq` | original key | 1 | 30 days | ingestor, lake-writer | none automatic; inspected by `make dlq` |

- **Serialization:** UTF-8 JSON of the Pydantic `IngestionEvent`, plus Kafka headers `schema_version`, `domain`, `source`, `event_id`, `run_id`. JSON Schema is exported from the Pydantic models into `docs/schemas/` for review. There's no Schema Registry or Avro in Phase 2 (ADR-010).
- **Schema versions:** `schema_version` is an integer per domain contract, currently `1`. Consumers keep a supported-versions table. An unknown version is a permanent failure and goes straight to the DLQ; it is never guessed.
- **Payload in the message.** News metadata, OHLCV bars and company rows are well under 64 KB, so the full record travels in the message, capped by `KAFKA_MAX_MESSAGE_BYTES` at 1 MB. ADR-010's claim-check pattern (bodies in S3, IDs in Kafka) becomes necessary once filings and full documents are ingested; that's recorded as a later change.
- **Consumer:** the lake-writer consumes all three raw topics, with `enable.auto.commit=false`. Offsets are committed **after** the raw object is written and the `ingestion_records` row is updated (or after a DLQ publish succeeds). Delivery is at-least-once.

## 5. Raw data lake design

- **Interface** (`app/pipeline/lake/`): `put_if_absent(key, bytes) -> PutResult(created|exists)`, `get(key)`, `list(prefix)`, `uri(key)`. There are two backends behind it:
  - **`S3ObjectStore`** (boto3) is the target. It runs against AWS S3 or **MinIO** locally, and is the only backend in compose.
  - **`LocalObjectStore`** is a filesystem root with the same key layout. It is used only by unit and Spark tests, so they don't need MinIO. It is the same architecture, not a separate storage design.
- **Layout.** Partitions use the **ingestion** date (UTC), because raw is append-only by arrival time:

```
s3://quantpilot-lake/raw/news/year=2026/month=09/day=27/<event_id>.json
s3://quantpilot-lake/raw/market/year=2026/month=09/day=27/<event_id>.json
s3://quantpilot-lake/raw/company/year=2026/month=09/day=27/<event_id>.json
s3://quantpilot-lake/processed/<domain>/year=/month=/day=/part-*.parquet   # partitioned by record date
s3://quantpilot-lake/rejects/<domain>/job_id=<uuid>/part-*.parquet          # Spark-level rejects
s3://quantpilot-lake/_manifests/processing/<job_id>.json
```

- **One object per event.** The object is the full `IngestionEvent` (original payload, source, `ingested_at`, `external_id`, `content_hash`, `schema_version`, `run_id`), not a giant file.
- **Immutability.** Keys are the deterministic `event_id`, so a redelivered message maps to the same key. Writes use `If-None-Match: *` (conditional put, supported by S3 and MinIO), so an existing object is never overwritten. The MinIO bucket also has versioning enabled as a second guard. Changed content means a new `content_hash`, which means a new `event_id` and a new object; history is preserved.
- **Reproducibility.** Everything downstream (processed Parquet and the canonical rows) can be rebuilt from `raw/` alone with `make process` then `make load`.
- **Known cost.** Small files. That's acceptable at this volume (hundreds to thousands of objects per day). Compaction is a later-phase job.

**As built (Step 2.3).**
- **Package.** The code lives in `app/pipeline/lake/`:
  - `base.py` defines the `ObjectStore` interface, which has `put_if_absent`, `get`, `head`, `exists`, `list`, `uri` and `check`.
  - `keys.py` builds and validates the key layout above.
  - `local.py` is `LocalObjectStore`; `s3.py` is `S3ObjectStore`.
  - `build_object_store()` picks the backend from `LAKE_BACKEND`.
- **Refused writes.** `put_if_absent` never replaces an object. When the key is already taken, it returns `created=False` and the stored object's SHA-256. The caller decides what differing bytes mean. This can happen when the same `event_id` is re-sent in a later run with a new `run_id` or `ingested_at`. In that case the first copy that landed wins.
- **Local backend.** Bytes are staged in a `.tmp-*` file, fsynced, then hard-linked to the final name (`os.link`, which is atomic and fails if the name exists). The final file is made read-only. Key segments may not start with `.`, which rules out `..` traversal.
- **S3 backend.**
  - Every put sends `If-None-Match: *` and `ChecksumSHA256`, and stores `x-amz-meta-sha256`. A 412 response means the object exists. Reads are checked against the stored hash.
  - Botocore's own retries are off, so `call_with_retry` owns all retries. It retries 409 `ConditionalRequestConflict` (a concurrent writer), 5xx responses, throttling and connection errors, with bounded backoff. Permanent errors, such as 403, are raised immediately.
  - Credentials come only from the standard AWS chain.
- **`python -m app.pipeline lake init|check`.**
  - `init` creates the bucket and enables versioning; it is idempotent.
  - `check` reports the bucket and versioning status, then runs a write-once probe: it tries to overwrite `_health/write-once-probe.json` and checks that the original is intact. This catches S3-compatible servers that silently ignore `If-None-Match`.
- **MinIO image.** Docker Hub and quay.io no longer serve `minio/minio` or `minio/mc`, so compose uses Chainguard's `minio` and `minio-client` images. The free tier only publishes `latest`, so both are pinned by digest. The images have no shell, so setup is two one-shot `mc` services: `minio-ready` runs `mc ready`, then `minio-init` runs `mc mb --ignore-existing --with-versioning`.
- **Test isolation.** `tests/conftest.py` pins `LAKE_BACKEND=local`, `LAKE_LOCAL_ROOT=backend/tests/_test_lake/` and a `quantpilot-test-*` bucket name before any app import. The `s3` tier creates a fresh bucket per test and deletes all its object versions afterwards.

## 6. Data schemas (canonical contracts)

These are Pydantic v2 models with `extra="forbid"` and timezone-aware UTC datetimes, in `app/pipeline/contracts.py`. Records are typed end to end; the only untyped field is `IngestionEvent.original`, which intentionally holds the verbatim source payload.

```python
class IngestionEvent(BaseModel):          # the Kafka message and the raw lake object
    schema_version: int                   # envelope version (1)
    event_id: str                         # sha256(source | external_id | content_hash)
    run_id: UUID                          # ingestion_runs.id
    source: str                           # adapter key, e.g. "yfinance.market_daily"
    source_type: DataSourceType           # news | market_data | reference_data
    domain: Domain                        # news | market | company
    external_id: str
    dedup_key: str                        # logical identity (see §7)
    content_hash: str                     # sha256 of the canonical record's normalized fields
    occurred_at: datetime | None          # published_at / bar timestamp; None for companies
    ingested_at: datetime
    record: NewsRecord | MarketRecord | CompanyRecord   # discriminated by `domain`
    original: dict[str, Any]              # verbatim source payload, kept for reproducibility

class NewsRecord(BaseModel):
    external_id: str; source: str; title: str; url: HttpUrl
    published_at: datetime; author: str | None; publisher: str | None
    summary: str | None                   # description; no full-article bodies in Phase 2
    tickers: list[str]                    # symbols the source associated; not entity-resolved
    content_hash: str; ingested_at: datetime

class MarketRecord(BaseModel):
    symbol: str; interval: Literal["1d"]; timestamp: datetime   # bar open, UTC
    trading_date: date                    # exchange-local session date (partition + identity)
    open: Decimal; high: Decimal; low: Decimal; close: Decimal; adj_close: Decimal | None
    volume: int; currency: str | None; source: str; ingested_at: datetime
    # validators: all prices > 0 and finite, low <= min(open, close) <= max(open, close) <= high, volume >= 0

class CompanyRecord(BaseModel):
    external_id: str                      # CIK (10 digits)
    name: str; ticker: str | None; exchange: str | None; cik: str
    source: str; ingested_at: datetime

class DeadLetter(BaseModel):              # value on quantpilot.ingestion.dlq
    schema_version: int; dlq_id: UUID; component: Literal["ingestor", "lake_writer", "loader"]
    source: str | None; original_topic: str | None; partition: int | None; offset: int | None
    original_key: str | None; original_value: str          # utf-8 text, or base64 with encoding flag
    value_encoding: Literal["utf8", "base64"]
    event_id: str | None; run_id: UUID | None
    failure_reason: Literal["deserialize", "unsupported_schema_version", "envelope_invalid",
                            "payload_invalid", "hash_mismatch", "retries_exhausted", "oversize"]
    error_type: str; error_message: str; error_details: list[dict]   # e.g. pydantic errors()
    retry_count: int; first_failed_at: datetime; failed_at: datetime
```

`DataSourceType` is a pipeline-level enum. Its `news` and `market_data` values match `app.domain.enums.SourceType`, so news maps straight onto `sources.source_type`. `reference_data` (the SEC company list) never lands in `sources`, so the Phase 1 enum and its CHECK constraint stay unchanged.

Each domain has its own envelope type (`NewsEvent`, `MarketEvent`, `CompanyEvent`), and they are discriminated by `domain`, so the record is typed without casts. The generated JSON Schemas live in `docs/schemas/*.v1.json`. `python -m app.pipeline schemas --check` fails when they drift from the models, and a unit test runs that check.

## 7. Deduplication strategy

The system uses three identities, each computed **once** by the shared Python contracts module and carried downstream. Spark never re-derives them with different logic.

| Identity | Formula | Meaning | Enforced at |
|---|---|---|---|
| `dedup_key` (logical record) | news: `news:` + `url_hash(normalize_url(url))`; market: `market:{symbol}:{interval}:{trading_date}:{source}`; company: `company:cik:{cik}` | "The same thing in the world" | Kafka key, Spark window key, canonical UNIQUE constraints |
| `content_hash` (version) | sha256 of canonical JSON (sorted keys, normalized fields, excluding `ingested_at`/`run_id`) | "Same content" | `event_id` input |
| `event_id` (observation) | sha256(`source` \| `external_id` \| `content_hash`) | "We already received this exact version" | `ingestion_records` UNIQUE, raw object key |

| Stage | Duplicate rule |
|---|---|
| Ingestor | `event_id` already in `ingestion_records` with status ≥ `published` → counted `duplicate`, not republished |
| Lake-writer | Conditional put: the object already exists → `exists`. Idempotent and not an error |
| Spark | Window over `dedup_key`; keep the newest `ingested_at`, tie-broken by `event_id` ascending. Deterministic; the number removed is reported as `duplicates_removed` |
| Loader | `ON CONFLICT (natural key) DO UPDATE … WHERE excluded.source_ingested_at > table.source_ingested_at`. Replaying older data never overwrites newer data |

The rule "same record + same content hash → no duplicate canonical row" is therefore enforced at four independent points. The canonical UNIQUE constraint is the final guarantee.

## 8. PySpark processing design

- **Runtime:** PySpark in `local[*]` mode. There's no cluster, no Structured Streaming and no Delta/Iceberg in Phase 2; that's the "no unnecessary Spark complexity" rule. It runs in a dedicated `spark` image (python:3.11-slim + OpenJDK 17 JRE + pyspark + the hadoop-aws jars baked in at build time for `s3a://` → MinIO). The version is pinned during implementation after a compatibility check against Python 3.11 and the hadoop-aws jars.
- **One generic job with per-domain specs** (`app/pipeline/spark/`): `process(domain, since=None)`. Each domain spec supplies an explicit `StructType`, normalization expressions, validity predicates, the output partition column, and the output columns. News and market are the required jobs; company uses the same template at almost no extra cost.
- **Steps:**
  1. Read `raw/<domain>/` (optionally only ingest-date partitions ≥ `since`) with the explicit schema, `mode=PERMISSIVE` and a `_corrupt_record` column.
  2. Validate. Corrupt JSON, an unsupported `schema_version`, a null required field or invalid numerics are written to `rejects/` with a `reject_reason`. They are counted, not dropped silently.
  3. Normalize: UTC timestamps, upper-case symbols, trimmed strings, `Decimal` prices as `DECIMAL(20,6)`.
  4. Deduplicate (§7). In incremental mode, the job unions the already-processed rows of affected partitions before deduplicating (merge-on-read).
  5. Write Parquet partitioned by record date (`published_at` for news, `trading_date` for market) with **dynamic partition overwrite**. Rerunning on the same input produces the same partitions and rows.
  6. Emit metrics (`input_rows`, `rejected`, `duplicates_removed`, `output_rows`, `output_partitions`, `duration_ms`) to the `processing_jobs` row, `_manifests/processing/<job_id>.json`, and a `pipeline.spark.job_finished` log event.
- **Lineage columns on every processed row:** `event_id`, `raw_uri`, `run_id`, `source`, `source_ingested_at`, `processing_job_id`.
- **Honest scope:** today's volume fits in one Python process. Spark is here because this phase explicitly calls for a scalable batch layer, and the job is structured so the same code handles years of history. Measured row counts and durations get documented next to the job, as ADR-011 requires; no performance claims are made without measurement.

## 9. PostgreSQL loading strategy

Migration **`0005_ingestion_pipeline`** uses the Phase 1 conventions: UUID PKs, `created_at`/`updated_at`, VARCHAR + CHECK enums, named constraints, a working downgrade, and it must work on both SQLite and Postgres.

```
ingestion_sources   id, key UNIQUE, domain [news|market|company], provider, description,
                    enabled, created_at, updated_at             -- upserted from the adapter registry, not seeded by migration

ingestion_runs      id, source_id FK→ingestion_sources, trigger [cli|make|test], params JSON,
                    status [PENDING|RUNNING|COMPLETED|PARTIAL|FAILED],
                    records_received, records_valid, records_invalid, records_duplicate,
                    records_published, records_publish_failed, error NULL,
                    started_at, finished_at NULL, duration_ms NULL, created_at
                    INDEX(source_id, started_at)

ingestion_records   id, event_id UNIQUE, run_id FK→ingestion_runs, source_id FK,
                    domain, external_id, dedup_key, content_hash, schema_version,
                    status [pending|published|publish_failed|landed|dlq|processed|loaded],
                    status_reason NULL, kafka_topic, kafka_partition NULL, kafka_offset NULL,
                    raw_uri NULL, processing_job_id NULL FK→processing_jobs,
                    first_seen_at, updated_at
                    INDEX(dedup_key), INDEX(run_id, status)

processing_jobs     id, job_name, domain, mode [full|incremental], since NULL,
                    status [RUNNING|COMPLETED|FAILED], input_rows, rejected_rows,
                    duplicates_removed, output_rows, output_partitions JSON, output_uri,
                    manifest_uri, code_version (git sha), engine_version, error NULL,
                    started_at, finished_at NULL, duration_ms NULL

market_bars         id, symbol, interval, trading_date, bar_ts, open, high, low, close,
                    adj_close NULL, volume BIGINT, currency NULL, source,
                    content_hash, source_ingested_at, event_id, raw_uri,
                    processing_job_id FK→processing_jobs, created_at, updated_at
                    UNIQUE(symbol, interval, trading_date, source)  INDEX(symbol, trading_date)

news_articles       source_id PK/FK→sources (1:1 extension, like companies→entities),
                    provider, external_id, author NULL, summary NULL, tickers JSON,
                    source_ingested_at, event_id, processing_job_id FK, created_at, updated_at
                    UNIQUE(provider, external_id)

sources   (+)       ingestion_record_id NULL FK→ingestion_records     -- raw_uri already exists
companies (+)       source_ingested_at NULL, event_id NULL, processing_job_id NULL FK
```

**What goes where.**
- **News** is a document, so it lands in `sources` (`source_type='news'`: url, url_hash, title, publisher, published_at, content_hash, **raw_uri**) with news-specific fields in `news_articles`. There is no second copy of the URL, title or date. Later phases' `evidence_records` then attach to the same `sources` row.
- **Companies** go through `get_or_create_company` into `entities` + `companies`, keyed by CIK. SEC lists some CIKs with several tickers (e.g. share classes). The first listed ticker becomes `companies.ticker`; the rest stay in raw and processed data, and the limitation is documented.
- **Market bars** have no Phase 1 home (`market_event_windows` is derived analytics), so they get the new table `market_bars`. The prompt's `market_data` name is used as the dataset/domain name.

**The loader** (`python -m app.pipeline load --domain market [--job <id>]`) reads the processed partitions listed in a job manifest with pyarrow. It doesn't use Spark JDBC, which can't upsert. It writes batched `INSERT … ON CONFLICT DO UPDATE` with the newest-wins guard, one transaction per partition, then marks the `ingestion_records` rows `loaded`. Retries are safe: a reload is a no-op.

## 10. Idempotency strategy

Exactly-once delivery is **not** claimed. The pipeline is at-least-once everywhere, with idempotent effects, which gives **effectively-once canonical data**.

| Operation | Why a retry is safe | Where exactly-once is impossible |
|---|---|---|
| API fetch | Fetching has no side effects. The deterministic `event_id` makes re-fetching the same data a duplicate | Upstream APIs have no idempotency tokens; the data may change between retries, which is recorded as a new version |
| Kafka publish | Idempotent producer (no broker-side duplicates from client retries within a session); `ingestion_records` status prevents republishing | A crash after a broker ack but before the DB status update republishes the event. The Kafka write and the Postgres write aren't atomic, and there's no transactional outbox in Phase 2. Consumers absorb it |
| Kafka consume | Side effects happen before the offset commit; each effect is idempotent | A crash between the effect and the commit causes redelivery. Kafka transactions only cover Kafka→Kafka, and our sinks are S3 and Postgres |
| Raw storage | Deterministic key + conditional put | none: write-once per `event_id` |
| Spark | Deterministic dedup and dynamic partition overwrite of the same partitions | A failed job can leave partially written partitions until it's rerun. The job is `FAILED` in `processing_jobs`, and loaders only read manifests of `COMPLETED` jobs |
| Postgres load | UNIQUE natural keys, ON CONFLICT, newest-wins guard | none at row level. Partitions are transactional, and a job is `loaded` only after every partition commits |

## 11. Retry / DLQ strategy

- **One retry utility** (`app/pipeline/retry.py`): `RetryPolicy(max_attempts=5, base_delay=0.5s, max_delay=30s, jitter=full)` from settings, plus an injectable `sleep` so unit tests don't actually wait. It retries only errors classified as **transient** (timeouts, connection errors, HTTP 429/5xx, S3 5xx/throttling, Postgres `OperationalError`). **Permanent** errors (validation, unsupported schema, oversize, deserialization) are never retried.
- **Ingestor:** transient fetch errors are retried. On exhaustion the run records the error and ends `PARTIAL` (some symbols succeeded) or `FAILED` (none did). Conversion failures go to the DLQ (`component="ingestor"`, `retry_count=0`).
- **Lake-writer:**
  - Permanent failure → DLQ immediately, then commit the offset.
  - Transient failure → retry in place. On exhaustion → DLQ with `retries_exhausted` and the retry count, then commit.
  - **If publishing to the DLQ fails, the offset is not committed and the consumer stops with a non-zero exit.** Nothing is lost silently; the message is redelivered after a restart.
- **Loader:** transient database errors are retried. On exhaustion the job fails loudly and the affected `ingestion_records` keep their status, so a rerun resumes.
- **Inspection:** `make dlq` prints recent DLQ records (reason, component, source, event_id, error). DLQ rows are also reflected in `ingestion_records.status='dlq'` whenever an `event_id` could be parsed. Replaying from the DLQ is manual (`make dlq-replay id=…`, stretch goal).

## 12. Testing strategy

Every tier runs on isolated resources and is **skipped unless explicitly configured**. The Phase 1 guarantees stay: the root `conftest.py` still pins a throwaway SQLite file, and the guard test still passes.

| Tier (marker) | Needs | Covers | Isolation |
|---|---|---|---|
| unit (none) | nothing | contracts/validators, hashing and identities, dedup ordering, serde round-trip, retry/backoff (fake sleep), transient-vs-permanent classification, DLQ envelope, lake key layout, `LocalObjectStore` write-once, run status derivation, adapter mapping from recorded payload fixtures | tmp dirs |
| integration | `TEST_DATABASE_URL` (existing) | `0005` drift check and round-trip, upserts (newest-wins, no duplicates), run tracking, loader from a Parquet fixture written with pyarrow, lineage query | existing schema reset + SAVEPOINT rollback |
| `kafka` | `TEST_KAFKA_BOOTSTRAP` | producer → consumer, redelivery without duplicates, malformed → DLQ, DLQ-publish failure doesn't commit | per-test topic names `test.<uuid>.*` and unique consumer groups; deleted in teardown |
| `s3` | `TEST_S3_ENDPOINT` (MinIO) | `S3ObjectStore` conditional put, list/get, immutability | per-session bucket `quantpilot-test-<uuid>`, emptied and deleted |
| `spark` | Java 17 | raw → processed Parquet, dedup determinism (twice → identical output), rejects, partitioning, manifest | `LocalObjectStore` in tmp dirs, `local[1]` |
| `pipeline` | all of the above | fixture ingest → Kafka → raw → Spark → Parquet → Postgres; the same input twice → identical canonical row counts; malformed input → DLQ and no canonical row | all of the above |

- **Fixtures.** Adapter tests use small recorded payloads, clearly labelled `fixtures/<source>/…` with a README noting the capture date. Pipeline tests use synthetic records named as synthetic. No fixture is ever loaded into the development database or presented as real data.
- **Guards.** A new guard test asserts that the pipeline tiers resolve only `TEST_*` variables and refuse topic prefixes other than `test.`, bucket names other than `quantpilot-test-*`, and Postgres database names without `test`.
- **CI:** existing jobs plus a `pipeline` job: `actions/setup-java@v4` (Temurin 17), Kafka and MinIO started with `docker run` (MinIO needs a `server` command, which GitHub service containers can't pass), then all tiers. The pyspark and jar downloads are cached.

## 13. Local development architecture

```
make up              unchanged: postgres, redis, migrate, backend, frontend
make pipeline-up     + kafka (KRaft), kafka-init (topics), minio, minio-init (bucket + versioning), lake-writer
make kafka-up        kafka + kafka-init only
make lake-up         minio + minio-init only
make ingest source=market symbols=AAPL,MSFT,NVDA start=2026-09-01
make ingest source=news symbols=AAPL
make ingest source=company
make process domain=market        # docker compose run --rm spark …
make load domain=market
make pipeline                     # ingest (all sources) → wait for lake-writer lag 0 → process → load → summary
make runs | make dlq | make lineage table=market_bars id=<uuid>
make pipeline-test                # kafka + s3 + spark + pipeline tiers against pipeline-up resources (test topics/bucket)
```

- The new services live under the compose profile `pipeline`, so the default stack is unchanged. Startup is ordered by healthchecks: kafka healthy → `kafka-init` completed → lake-writer; minio healthy → `minio-init` completed.
- Host ports are overridable (`KAFKA_PORT`, `MINIO_PORT`, `MINIO_CONSOLE_PORT`), following the Phase 1 pattern.
- MinIO credentials in compose are dev-only placeholders, like the Postgres password. Real AWS credentials come only from the standard AWS environment variables or an IAM role and are never committed.
- **Dependencies are split** so the API image doesn't grow:
  - `requirements.txt` (API) is unchanged.
  - `requirements-pipeline.txt` adds `confluent-kafka`, `boto3` and `pyarrow`.
  - `requirements-spark.txt` adds `pyspark`.
  - `requirements-dev.txt` includes the pipeline and Spark requirements.
- **Spark runs in Docker by default.** Running Spark tests on the host needs a JDK 17 (§14, decision 1).
- **New settings** (typed, in `Settings`):
  - Kafka: `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC_{NEWS,MARKET,COMPANY}_RAW`, `KAFKA_TOPIC_DLQ`, `KAFKA_CONSUMER_GROUP_LAKE`, `KAFKA_MAX_MESSAGE_BYTES`.
  - Lake: `LAKE_BACKEND` (`s3`/`local`), `LAKE_BUCKET`, `LAKE_ENDPOINT_URL`, `LAKE_REGION`, `LAKE_LOCAL_ROOT`.
  - Ingestion and retries: `INGEST_SYMBOLS`, `PIPELINE_RETRY_MAX_ATTEMPTS`, `PIPELINE_RETRY_BASE_SECONDS`, `PIPELINE_RETRY_MAX_SECONDS`.
  - `SEC_EDGAR_USER_AGENT` is reused.
- **Readiness:** the API's `/ready` does **not** depend on Kafka or MinIO, because the API works without the pipeline. `make pipeline-status` reports broker, bucket, consumer lag and the last runs instead.

## 14. Risks and tradeoffs

| Risk / tradeoff | Mitigation / stance |
|---|---|
| **No JVM on the host**, and PySpark requires one | Spark jobs run in the `spark` image; the host JDK is optional (decision 1); CI installs Temurin 17 |
| Spark is oversized for today's data volume | Kept to `local[*]` with one generic job and no streaming. Measured numbers are documented; the value claimed is the scalable pattern, not speed |
| `s3a` jar/version coupling (hadoop-aws ↔ Spark's Hadoop ↔ AWS SDK bundle, ~300 MB) | Pinned together and baked into the image at build time; verified by the `s3` + `spark` e2e run |
| yfinance is an unofficial Yahoo API (schema drift, throttling, ToS for research use) | Adapter isolation, bounded retries, recorded fixtures, `original` payload kept. No redistribution, no full-article scraping |
| SEC fair-access rules (User-Agent, ~10 req/s) | Reuse `SEC_EDGAR_USER_AGENT`; the company adapter makes a single request |
| Kafka + Postgres dual write isn't atomic (no outbox) | Documented; downstream idempotency absorbs duplicates; the outbox pattern is a later hardening item |
| Single broker, RF=1 locally | A dev-only durability limit; raw S3 is the durable record once landed; production uses a managed Kafka with RF≥3 (deploy phase) |
| One object per raw event (small files) | Fine at portfolio volume; compaction is deferred and documented |
| Payload in the message vs claim-check | Right for small records now; claim-check arrives with document ingestion |
| Dev default DB is still SQLite | The migration stays portable; `make pipeline` targets compose Postgres (`DATABASE_URL` set by compose); the SQLite suite keeps running |
| Test runtime and CI complexity grow | New tiers are opt-in locally and run in a separate CI job |
| `trading_date` vs timestamp time-zone mistakes for daily bars | Identity uses the exchange-local session date from yfinance's index; timestamps stored in UTC; covered by unit tests |
| CIKs with several tickers vs `companies.cik UNIQUE` | First listed ticker becomes canonical; the others stay in raw and processed data; documented |
| Scope creep toward Airflow, agents or graph | Out of scope; runs are CLI/Make-triggered; nothing downstream consumes canonical tables yet |

## 15. Implementation phases (each step: code + tests + green suite before the next)

| Step | Deliverable | Verification |
|---|---|---|
| **2.1** | `app/pipeline/` skeleton: `contracts.py` (IngestionEvent, News/Market/Company records, DeadLetter), identities/hashing, serde, validation, `retry.py`; pipeline settings in `Settings`; JSON Schema export | unit tier; full SQLite suite; `alembic check` unchanged |
| **2.2** | Migration `0005` + models + repositories (ingestion sources/runs/records, processing_jobs, market_bars, news_articles, lineage columns); run-tracking service | integration tier on Postgres; `alembic check` on SQLite + Postgres; round-trip |
| **2.3** | Lake: interface, `LocalObjectStore`, `S3ObjectStore`; compose `minio` + `minio-ready` + `minio-init`; `make lake-up` / `lake-check` / `test-s3` | unit + `s3` tier ✅ |
| **2.4** | Kafka: compose `kafka` + `kafka-init`; producer, consumer loop, DLQ publisher; lake-writer service; `make kafka-up` | `kafka` tier (redelivery, malformed → DLQ, DLQ-failure no-commit) |
| **2.5** | Source adapters (yfinance market, yfinance news, SEC companies) + `ingest` CLI + run tracking | unit (fixtures); live smoke on real data → raw objects in MinIO |
| **2.6** | Spark image + generic job (market, news, company) + rejects + manifests + `processing_jobs` | `spark` tier; `make process` on the live raw data |
| **2.7** | Loader + lineage CLI + `make runs/dlq/lineage` | integration + `pipeline` tier; duplicate run → identical counts |
| **2.8** | `make pipeline` end to end on real data; CI pipeline job | full verification (below) |
| **2.9** | Docs: README, this file as-built, ADR-009/010/011 amendments + new ADR-020 (effectively-once pipeline), `ENV_VARIABLES.md`, `backend/app/README.md`, `stack.ts` | frontend tsc + lint |

**Definition of done** is the checklist in the Phase 2 brief, verified on a real end-to-end run:
- live yfinance/SEC data flows through Kafka into MinIO raw objects, then processed Parquet, then canonical Postgres rows;
- rerunning the same ingestion leaves canonical counts unchanged;
- a malformed message lands in the DLQ with full metadata;
- every canonical row traces back to its source through the lineage command;
- all tiers pass, the dev database fingerprint is unchanged by tests, and git status is clean.

**Exact first implementation step (after approval):** Step 2.1. Create `backend/app/pipeline/__init__.py`, `contracts.py`, `identity.py` (thin wrappers over `app/domain/identity.py` plus the market and company key rules), `serde.py`, `validation.py` and `retry.py`. Add the pipeline settings to `app/core/settings.py`, and add `backend/tests/unit/pipeline/` covering the contracts, hashing, dedup ordering, serde round-trip and retry policy. No infrastructure, migrations or new dependencies yet: this step uses only Pydantic and the stdlib.
