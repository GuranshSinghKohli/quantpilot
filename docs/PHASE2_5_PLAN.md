# QuantPilot — Phase 2 Step 2.5: Real Source Adapters + Ingestion Orchestration

Baseline: `97787f9 feat: add Kafka ingestion backbone and lake writer`. Step 2.4 delivered the transport (tracked publisher → Kafka → lake writer → write-once raw lake → `ingestion_records`) but only SYNTHETIC events flowed through it. Step 2.5 adds three real external sources in front of that pipeline. Nothing behind the publisher changes.

```
External API (Massive REST, SEC)
      │  SourceClient: throttle → GET → classify status → bounded retry (call_with_retry)
      ▼
SourceAdapter.streams(request) → pages of SourceItem(original = verbatim provider object)
      │  adapter.normalize(item) → NewsRecord | MarketRecord | CompanyRecord   (contracts.py)
      │  invalid → counted `invalid` + DLQ (component "ingestor"); never dropped
      ▼
build_event(record, original=item.original)       deterministic event_id
      │  in-run dedup by event_id
      ▼
EventPublisher (Step 2.4, unchanged): claim_event → Kafka (acks=all) → published / publish_failed
      ▼
Kafka → Lake writer → raw lake (event with verbatim `original`) → ingestion_records landed
```

The adapter never touches Kafka, Postgres or the lake. `IngestionRunner` is the only piece that knows about all of them, and it only calls existing components (`RunTracker`, `EventPublisher`, `DeadLetterPublisher`, `build_event`).

## 1. What already existed and is reused

| Existing piece | Used for |
|---|---|
| `contracts.py` records + `build_event` | Normalized record per source; identities (`event_id`, `dedup_key`, `content_hash`) computed once there |
| `IngestionEvent.original` | Raw preservation: the verbatim provider object travels in the event and lands in the raw lake |
| `RunTracker` / `derive_run_status` (2.2) | One `ingestion_runs` row per adapter execution; COMPLETED / PARTIAL / FAILED |
| `EventPublisher` (2.4) | Claim → publish → delivery-confirmed status; cross-run duplicate detection |
| `dead_letter` / `DeadLetterPublisher` | Invalid provider records → DLQ with the verbatim item |
| `retry.call_with_retry` + `RetryPolicy.from_settings` | All HTTP retries. One small extension: an error may carry `retry_after`, which raises the wait (Retry-After) |
| `Metrics` | Per-provider request, latency, rate-limit and record counters |
| `Settings` | Provider configuration; secrets are `SecretStr` |
| `httpx` (already an API dependency) | HTTP client; `httpx.MockTransport` for tests. No new dependency |

## 2. Provider selection

| Domain | Provider | Endpoint | Why |
|---|---|---|---|
| News | **Massive** (formerly Polygon.io) | `GET /v2/reference/news` | Documented REST API, free "Stocks Basic" plan includes it (2 years of history, updated hourly), stable per-article `id`, RFC 3339 `published_utc`, `published_utc.gte/.lte` windows, `ticker` filter, `limit` ≤ 1000, cursor pagination via `next_url` |
| Market | **Massive** | `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` | Documented, free plan includes end-of-day daily bars (2 years), explicit `adjusted=false` for raw prices, `next_url` pagination |
| Company | **SEC** | `GET https://www.sec.gov/files/company_tickers_exchange.json` | Keyless, authoritative CIK, name, ticker, exchange; one document (~10.4k rows) |

**Rejected.** yfinance (the approved Phase 2 plan's choice) is an unofficial scraper of Yahoo endpoints: undocumented, no status-code contract (no 401/429 semantics to test), no windows or pagination for news. It stays as the API's quote/news tool; it is not an ingestion source. NewsAPI.org (developer key restricted to localhost use, no stable article id). Alpha Vantage (25 requests/day, rate limits reported as HTTP 200 bodies). GDELT (no pagination, frequent HTML error pages). Scraping is not used anywhere.

**One key, two sources.** News and market both use Massive, but they are configured independently (`NEWS_API_KEY`, `MARKET_API_KEY`), so either can move to another provider without touching the other. Setting both to the same key is normal.

## 3. Adapter architecture

`app/pipeline/sources/`:

| Module | Contents |
|---|---|
| `base.py` | `IngestRequest` (window, symbols, limits), `SourceItem` (verbatim `original` + non-secret `ref`), `Page`, `Stream`, `SourceAdapter` (abstract: `streams`, `normalize`, `health`, `close`), `NormalizationError`, `follow_cursor` (shared defensive pagination) |
| `http.py` | `SourceClient` (httpx + throttle + retry + status classification), `Throttle`, `SourceRequestError` / `TransientSourceError` / `PermanentSourceError`, `SourceFailure` kinds, `parse_retry_after` |
| `massive.py` | `MassiveNewsAdapter`, `MassiveMarketAdapter` |
| `sec.py` | `SecCompanyAdapter` |
| `registry.py` | Explicit `(domain, provider) → ProviderEntry` table, `build_adapter`, `missing_config`, `ConfigurationError` |
| `window.py` | `resolve_window` (defaults and bounds) |
| `../ingest.py` | `IngestionRunner`: the orchestration loop |

A **stream** is one independently failing unit of fetching: one ticker for news and market, the single document for SEC. A failed stream does not stop other streams unless the failure is run-fatal (authentication, or a rate-limit window longer than the budget).

Adapters return **items**, not events. `normalize(item) → Record` is pure (no I/O), so it is unit-tested directly against fixtures.

## 4. Source contracts and normalization

New source keys in `SOURCES` (the contract registry): `massive.news`, `massive.market_daily`. `sec.company_tickers` already existed. The unimplemented `yfinance.*` keys are removed (nothing was ever ingested under them; the test fixtures that used them now use the Massive keys). `synthetic.market` stays and is flagged `synthetic=True`.

| Source | `original` (raw layer, verbatim) | Normalized record |
|---|---|---|
| `massive.news` | the article object exactly as returned (`id`, `publisher{…}`, `insights[…]`, `keywords`, `amp_url`, `image_url`, …) | `NewsRecord`: `external_id`=`id`, `title`, `url`=`article_url`, `published_at` (UTC), `author`, `publisher`=`publisher.name`, `summary`=`description`, `tickers` (entries that are valid symbols) |
| `massive.market_daily` | `{"ticker", "adjusted", "request_id", "bar": {o,h,l,c,v,vw,n,t,otc?}}` — the bar verbatim plus the response fields that give it meaning | `MarketRecord`: `symbol`, `timestamp` = `t` (ms, UTC; the start of the ET session day), `trading_date` = `t` in America/New_York, `open/high/low/close`, `volume`, `adj_close=None`, `currency=None` |
| `sec.company_tickers` | `{"fields": [...], "data": [rows for this CIK]}` — every row for the CIK, verbatim | `CompanyRecord`: `cik` (10-digit), `name`, `tickers` (row order), `ticker` = first, `exchange` = first row's exchange |

**Explicit missing values (never fabricated).**
- Massive bars carry no currency and, with `adjusted=false`, no adjusted close: both are `None`, not guessed.
- A bar missing `o/h/l/c/v/t`, or with a fractional volume or inconsistent OHLC, is **rejected** (counted `invalid`, dead-lettered with the verbatim bar). It is not repaired.
- An article missing `title`, `article_url` or `published_utc` is rejected. A missing `author`/`description`/`publisher` is `None`.
- News `tickers` entries that are not valid equity symbols (e.g. `X:BTCUSD`) are left out of the normalized list; the raw `original` still has them.
- SEC rows with a null exchange give `exchange=None`. A malformed row (not a 4-item list, non-numeric CIK) becomes its own item and is rejected, not skipped.

**Company matching.** Identity is the CIK only (`company:cik:<cik>`), the Phase 2.2 rule. Rows are grouped by CIK, never by name. Two CIKs with the same name stay two companies.

## 5. Identity and deduplication rules

`event_id = sha256(source | external_id | content_hash)`; `content_hash` covers the normalized record without `ingested_at`. So the same provider record fetched twice gives the same `event_id`, and a changed record (e.g. corrected close) gives a new one.

| Source | `external_id` | `dedup_key` (logical thing) |
|---|---|---|
| `massive.news` | Massive article `id`. If absent: `derived:` + sha256(normalized URL \| publisher name \| published_at) | `news:<url_hash(article_url)>` (source-independent) |
| `massive.market_daily` | `{symbol}:1d:{trading_date}` | `market:{symbol}:1d:{trading_date}:massive.market_daily` |
| `sec.company_tickers` | 10-digit CIK | `company:cik:{cik}` |

Duplicate handling, in order:
1. **In the run:** the runner keeps the `event_id`s it has already handed to the publisher. The same article returned under two ticker streams, or twice on overlapping pages, is counted `duplicate` once.
2. **Across runs:** `EventPublisher` → `claim_event` finds the `event_id` in `ingestion_records` with a delivered status and skips it (`duplicate`). Nothing is republished; the lake is not touched.
3. **Downstream:** write-once lake keys and newest-wins canonical upserts (2.2/2.4), unchanged.

Running `make ingest-news` twice over the same window therefore publishes each article once. The second run is `COMPLETED` with `published=0, duplicate=N`.

**Known edge.** Massive may change fields that are not part of the normalized record (e.g. `insights`) without changing the article. The `event_id` stays the same and the second copy is a duplicate. The raw lake keeps the first observation. That is the intended write-once behaviour.

## 6. Windows, pagination and limits

Every run has an explicit, bounded window and a record cap. There is no "fetch everything" mode.

| Control | Setting / flag | Default | Bound |
|---|---|---|---|
| Window start / end | `--start`/`--end` (`NEWS_START`, `MARKET_START`, … in Make) | end = now, start = end − `INGEST_LOOKBACK_DAYS` | `INGEST_MAX_WINDOW_DAYS` (366); start < end; end ≤ now + 1 day |
| Records per run (all streams) | `--max-records` / `MAX_RECORDS` | `INGEST_MAX_RECORDS` = 1000 | 1–50 000 |
| Page size | `--page-size` | `INGEST_PAGE_SIZE` = 100 | 1–1000 (Massive max) |
| Pages per stream | `--max-pages` | `INGEST_MAX_PAGES` = 20 | 1–500 |

Date-only `--end` means the end of that UTC day. Market requests use calendar dates, and news requests use the exact instants (`published_utc.gte/.lte`).

`follow_cursor` (shared by both Massive adapters) is defensive:
- The cursor (`next_url`) is used only within the run, held in memory, and never logged (it is opaque and can be long). Only a short fingerprint is logged.
- `next_url` must point at the configured provider host. If it points anywhere else, the request is refused before any credential is sent.
- A repeated cursor is a provider error (`bad_response`), so the stream stops. Records already fetched are kept.
- An empty page that still has a cursor ends the stream (logged).
- `max_pages` stops the stream and marks it `truncated`.
- The record cap stops all fetching as soon as it is reached. Generators are lazy, so no extra request is made. The run summary says which streams were truncated.

**Checkpointing.** Cursors are not persisted across runs, on purpose. Resuming means rerunning the same window: deterministic `event_id`s make the overlap free (duplicates), so a checkpoint table would add state without adding safety. The run's `params` record the exact window and limits for reproduction.

## 7. Rate limiting and retries

`SourceClient` sits under every request.

**Client-side throttle.** Requests are spaced to `*_REQUESTS_PER_MINUTE`:

| Provider | Default | Reason |
|---|---|---|
| Massive | 5/min | The Stocks Basic plan limit |
| SEC | 600/min | SEC fair access is ≤ 10 req/s |

The throttle is per process, and runs are sequential. Running news and market ingestion at the same time on one free key would exceed the plan limit; the Makefile targets run one at a time.

**Failure classification.**

| Response | Kind | Retried? | Effect on the run |
|---|---|---|---|
| Timeout, connection reset/refused | `network` | yes | stream fails after retries are exhausted |
| HTTP 408, 5xx | `outage` | yes | stream fails after retries are exhausted |
| HTTP 429 with Retry-After ≤ `SOURCE_MAX_RETRY_AFTER_SECONDS` (120) | `rate_limited` | yes; waits at least Retry-After (seconds or HTTP date) | stream fails after retries are exhausted |
| HTTP 429 with a longer (or unparseable-and-exhausted) window | `rate_limited` | no | **run stops** (other streams would hit the same limit) |
| HTTP 401, 403 | `auth` | no | **run stops** |
| HTTP 400, 404, 422, other 4xx | `invalid_request` | no | that stream fails; others continue |
| 2xx that is not JSON, not the documented shape, or `status: ERROR` | `bad_response` | no | that stream fails |

Retries use the pipeline `RetryPolicy` (`PIPELINE_RETRY_*`: 5 attempts, full-jitter exponential backoff, 30 s cap). Each retry logs `pipeline.retry` with the operation, attempt, delay and `retry_after`. Exhaustion logs `pipeline.retry.exhausted`. Health checks never retry.

**Run status.**
- Everything fetched was published or was a duplicate, and no stream failed: `COMPLETED`.
- Some records got through, and a stream failed or records were invalid: `PARTIAL`.
- Nothing got through and something failed: `FAILED`.

The run error lists each failed stream as `label: kind: message`.

## 8. Run tracking and crash visibility

Every execution creates its `ingestion_runs` row through `RunTracker.start()` before the first request, and it is committed as `RUNNING`.
- `received` counts provider items. `valid` counts items that normalized, and `invalid` counts rejects. `published`, `duplicate` and `publish_failed` come from the publisher plus in-run duplicates. The 2.2 invariants (`received = valid + invalid`, `valid = published + duplicate + publish_failed`) are checked at finish.
- Any exception, including `KeyboardInterrupt`, finishes the run as `FAILED` with the error before it propagates.
- A hard kill (SIGKILL, power loss) leaves the row `RUNNING` with `finished_at` NULL, which is visible and queryable. Its `pending` records are re-published by the next run of the same window (Step 2.4 RETRY claims).

## 9. Configuration and secrets

All configuration lives in `Settings`; nothing reads `os.environ` directly. Keys are `SecretStr`: they never appear in `repr`, logs, run params or exceptions, and are sent only in the `Authorization: Bearer` header, never in URLs.

| Variable | Needed for | Default |
|---|---|---|
| `NEWS_PROVIDER` / `MARKET_PROVIDER` / `COMPANY_PROVIDER` | selecting the registry entry | `massive` / `massive` / `sec` |
| `NEWS_API_KEY` / `MARKET_API_KEY` | Massive | empty; `ingest news` / `ingest market` refuse to start without it |
| `NEWS_BASE_URL` / `MARKET_BASE_URL` / `COMPANY_BASE_URL` | pointing at the provider (or a local mock) | `https://api.massive.com`, `https://api.massive.com`, `https://www.sec.gov` |
| `SEC_EDGAR_USER_AGENT` | SEC (required by SEC fair-access policy) | empty; `ingest company` refuses to start without a contact-bearing value |
| `NEWS_TICKERS` / `MARKET_SYMBOLS` / `COMPANY_SYMBOLS` | the default query universe | `AAPL,MSFT,NVDA` |
| `*_REQUESTS_PER_MINUTE`, `SOURCE_TIMEOUT_SECONDS`, `SOURCE_MAX_RETRY_AFTER_SECONDS`, `INGEST_*` | throttling, timeouts, bounds | see `ENV_VARIABLES.md` |

- Base URLs must be `https://`. Plain `http://` is accepted only for loopback, `host.docker.internal` and single-label compose service names, so a local mock provider works but a real key is never sent in clear text over a network.
- The API process never builds an adapter, so it starts without any provider credential. Only `python -m app.pipeline ingest …` validates them, and it fails with a message naming the missing variable.

## 10. Commands

```bash
make ingest-news    [NEWS_TICKERS=AAPL] [NEWS_START=2026-09-20] [NEWS_END=2026-09-26] [MAX_RECORDS=200]
make ingest-market  [MARKET_SYMBOLS=AAPL,MSFT] [MARKET_START=2026-09-01] [MARKET_END=2026-09-25]
make ingest-company [COMPANY_SYMBOLS=AAPL,GOOGL | COMPANY_ALL=1 MAX_RECORDS=20000]
make sources-check  # configured / reachable / auth / rate-limit headers per source; separate from /health
```

These run `python -m app.pipeline ingest <domain> …` and `sources check` in the compose `ingest` service. That service has the pipeline image, the compose Postgres and Kafka, and the provider variables interpolated from the shell or `.env`. It is in its own `ingest` profile, so `make pipeline-up` never starts it and the lake writer never sees provider keys.

## 11. Source health

`python -m app.pipeline sources check [news|market|company]` reports:
- `configured`, and which variables are missing;
- `reachable`;
- `auth`: `valid`, `invalid` (401/403) or `not_required` (SEC);
- the HTTP status and latency;
- any rate-limit headers the provider returned, otherwise `null`.

Each check is one request and is never retried: Massive `news?limit=1` or a 5-day bar request, SEC `HEAD`. The API's `/health` and `/ready` do not call any provider.

## 12. Observability

These are structured log events; payloads and credentials are never logged.
- `pipeline.source.request`: provider, operation, status, latency_ms, attempt, rate-limit headers.
- `pipeline.retry` / `pipeline.retry.exhausted`: the retry utility.
- `pipeline.source.page`: source, provider, run_id, stream, page, items, has_more.
- `pipeline.source.record_rejected`: source, ref, error fields; no values.
- `pipeline.source.stream_finished`: stream, pages, fetched, truncated, error kind.
- `pipeline.source.run_summary`, plus the existing `pipeline.publish.batch` and `pipeline.ingest.run_finished`.

Metrics use `Metrics`:
- `source_requests_total{provider,status}`, `source_request_latency_seconds{provider}`;
- `source_rate_limited_total{provider}`, `source_throttle_wait_seconds_total{provider}`;
- `source_records_fetched_total{source}`, `source_records_invalid_total{source}`, `source_records_duplicate_total{source}`.

## 13. Test strategy

No test reaches a real provider. `tests/conftest.py` pins every provider key to empty and every base URL to the unroutable `http://127.0.0.1:9`, and a guard test checks it.

| Tier | Covers |
|---|---|
| unit, HTTP client | status → kind mapping; Retry-After in seconds and as an HTTP date; Retry-After over budget → no retry; 5xx and timeout retried then exhausted; 400/401/403 sent exactly once; throttle spacing; foreign `next_url` refused; the key only in the header, never in logs |
| unit, adapters (each of the three) | valid, empty and malformed responses; missing fields; duplicate records; pagination, repeated cursor and max pages; max records; timeout; 429; 500; 400; 401/403; deterministic event ids and no collisions; timestamp normalization; provider field preservation; run tracking; partial failure |
| unit, runner + registry | counters and invariants; second identical run is all duplicates; auth stops the run; invalid → DLQ; DLQ unavailable → the run error says so; crash → `FAILED` row; missing credentials → clear `ConfigurationError`; window bounds; synthetic vs real flags |
| kafka + s3 (`TEST_KAFKA_BOOTSTRAP` + `TEST_S3_ENDPOINT`) | mock provider (httpx `MockTransport`) → adapter → runner → real Kafka → lake writer → MinIO → `ingestion_records`: expected events, deterministic ids, raw objects containing the verbatim provider payload, lake URIs, run counts, statuses; the second run is all duplicates with no new objects |

**Fixtures** (`backend/tests/fixtures/sources/`, see its README):
- The Massive samples are the documented sample responses.
- The SEC subset is real rows captured from `company_tickers_exchange.json` on 2026-09-27.
- Error and edge-case responses are built in test code and named as such.

## 14. Local development

1. `make pipeline-up` (Step 2.4 stack).
2. Put `NEWS_API_KEY`, `MARKET_API_KEY` and `SEC_EDGAR_USER_AGENT` in the repo-root `.env` (git-ignored) or export them.
3. Run `make sources-check`, then `make ingest-company`, `make ingest-market` and `make ingest-news`.
4. Watch `make pipeline-logs` for `pipeline.lake_writer.landed`.

Without a Massive key, `ingest news` and `ingest market` exit with a configuration error, and `ingest company` still works (SEC is keyless).

## 15. Out of scope

PySpark (2.6), canonical loading (2.7), Neo4j, agents/RAG, scheduling, cross-run checkpoints, more providers. Canonical tables are still empty: duplicate protection is verified at `ingestion_records` and the raw lake, and canonical upsert idempotency was verified in 2.2.

## 16. As built and verification (2026-09-27)

**Automated.**
- Full suite with every tier enabled (SQLite, Postgres integration, S3/MinIO, Kafka): 661 passed, 0 skipped.
- New tests: 23 HTTP-client tests, 69 adapter tests, 33 runner/registry/CLI tests, and 2 Kafka + MinIO end-to-end tests.
- `python -m app.pipeline schemas --check`: match.
- No automated test makes a real network call. `conftest.py` points every provider base URL at `http://127.0.0.1:9`, and `test_suite_never_reaches_real_source_providers` enforces it.

**Live, against the compose stack (SEC only).**
- `make sources-check`: SEC healthy. News and market report "not configured" because no Massive key is present.
- `make ingest-company COMPANY_SYMBOLS=AAPL,MSFT,NVDA,GOOGL,BRK-B`:
  - First run: `COMPLETED`, 5 published.
  - Second run: 5 duplicates, 0 published.
  - The lake writer landed 5 objects under `raw/company/`. Each one holds the verbatim SEC rows: Alphabet 4 tickers, Berkshire 2.
  - Consumer lag is 0.
- `make ingest-news` without a key exits 2 with `NEWS_API_KEY is not set` before touching the database.

**Not verified live.** Massive news and market. No key was available, so these adapters are verified only against the documented sample responses and mocked HTTP. The first live run should be a small window (`MAX_RECORDS=50`), and its output should be compared with the fixture shapes.

**Known limitations.**
- The Massive free plan allows 5 requests/min, 2 years of history, and end-of-day data only. News and market share one key, so do not run them concurrently.
- Market events carry `adj_close=None` and `currency=None`, because the provider does not supply them per bar.
- Pagination cursors are not persisted. A rerun re-fetches the window and relies on deduplication.
- A hard kill (SIGKILL/OOM) leaves the run `RUNNING`. Any Python-level exception finishes it as `FAILED`.
- News tickers that are not equity symbols stay in the raw payload but not in `tickers`.
- An SEC company with any invalid ticker row is rejected whole to the DLQ rather than partially loaded.
- Canonical loading from the lake is not built (Step 2.7).
