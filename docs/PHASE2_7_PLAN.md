# Phase 2 · Step 2.7 — Canonical loading

Processed Parquet → **canonical loader** → validation → idempotent upsert → PostgreSQL.

This step adds the last hop of the Phase 2 data path. It reads the Parquet files that a
COMPLETED Step 2.6 processing job listed in its manifest, validates every row,
and upserts `companies` (+ `entities`), `market_bars` and `news_articles` (+ `sources`)
using the Phase 2.2 newest-wins rule. It does not read raw objects, call providers, publish to Kafka,
run Spark or serve HTTP.

## 1. What already existed (inspected before writing code)

| Piece | State before 2.7 | Used as |
|---|---|---|
| `app/repositories/canonical.py` (2.2) | `upsert_market_bars`, `upsert_news`, `upsert_companies`: `ON CONFLICT … DO UPDATE WHERE newer_than_existing` for bars and articles; companies resolved by CIK in Python with a savepoint per row | **The** write path. Extended, not duplicated |
| `app/repositories/_upsert.py` (2.2) | `newer_than_existing` (the only version rule), `newest_per_key`, `dialect_insert` | Unchanged |
| `app/domain/ingestion_schemas.py` (2.2) | `MarketBarIn`, `NewsArticleIn`, `CompanyListingIn` typed rows with lineage | Validation of every processed row |
| Migration 0005 | `market_bars` UNIQUE(symbol, interval, trading_date, source) + CHECK(low>0, low≤high, volume≥0); `news_articles` PK/FK → `sources` (url_hash UNIQUE); `companies` cik UNIQUE, UNIQUE(ticker, exchange); canonical `event_id` FK → `ingestion_records`, `processing_job_id` FK → `processing_jobs` | Correctness boundary; not weakened |
| `processing_jobs` (0005/0006) | processing status RUNNING/COMPLETED/FAILED; `loaded_rows`, `loaded_at`, `load_duration_ms` reserved for this step | Load tracking (extended by 0007) |
| `ingestion_records` | status … `processed` → `loaded` (forward-only `RECORD_TRANSITIONS`) | Marked `loaded` in the same transaction as the canonical write |
| Step 2.6 manifest | `_manifests/processing/<job_id>.json`: files (key, uri, size), partitions, counts (`output_rows` = rows in those files), schema version and columns | The only way the loader finds data |
| `jobs/lock.py` (2.6) | Postgres advisory lock per processed prefix | Reused: loading and reprocessing of one domain exclude each other |

Gaps that 2.7 closes:

1. **Tie-break mismatch.** Spark's dedup kept the *smaller* `event_id` on equal `ingested_at`, while `newer_than_existing` keeps the *larger*. Spark now uses the canonical order (one rule, stated once in `newer_than_existing`; Spark mirrors it and a test pins both).
2. **News was not atomic.** `upsert_news` read stored versions, decided in Python and updated `sources` with its own rule (`retrieved_at >=`). Under concurrency or on timestamp ties, `sources` and `news_articles` could come from different versions. It now takes the winner from the guarded `news_articles` upsert and then updates exactly those `sources` rows in the same transaction.
3. **Companies lost data.** `companies` had one ticker and no content hash, raw URI or source. Migration 0007 adds `tickers` (JSON), `source`, `content_hash` and `raw_uri`. Existing company rows are locked (`FOR UPDATE` on Postgres) before the version comparison.
4. **Counts.** `UpsertStats` only had written/unchanged. It now reports inserted / updated / stale (older than stored) / duplicate (same event already stored) / failed, plus the event IDs that are now canonical.

## 2. Architecture

```
processing_jobs (COMPLETED)                      advisory lock "processed/<domain>/"
        │ manifest_uri                           (same lock as Spark processing)
        ▼
_manifests/processing/<job_id>.json ── checks: kind, version, job/domain match,
        │ output.files[] (key, size)            processed_schema_version, every file present with the listed size
        ▼
LoadSource (pyarrow, one file at a time, record batches)
        │
        ├─ pass 1  VALIDATE (no writes): Parquet schema = processed schema; per row → *In model,
        │          identity recomputed (event_id, dedup_key), lineage present, FK targets exist
        │          any invalid row → load FAILED before anything is written
        │
        └─ pass 2  LOAD: per batch of ≤ LOAD batch rows (never spanning files):
                   BEGIN → domain upsert (Phase 2.2 newest-wins) → ingestion_records → loaded → COMMIT
                                    │
                                    ▼
             reconciliation → processing_jobs.load_* + write-once load report
             _manifests/loads/<job_id>/<attempt_id>.json
```

Package `app/pipeline/load/`:

| Module | Role |
|---|---|
| `source.py` | resolve job → manifest → files; stream Parquet record batches; partition-date filter |
| `validation.py` | shared lineage checks and the schema check (no DB writes) |
| `domains.py` | `CompanyLoader`, `MarketLoader`, `NewsLoader`: explicit column → field mapping, identity recomputation, delegate to `canonical.py` |
| `runner.py` | `CanonicalLoadRunner`: lock, lifecycle, batches, retry pass, reconciliation, report |
| `errors.py` | `LoadConfigError` (exit 2), `LoadValidationError`, `ReconciliationError` |

The loader is a CLI (`python -m app.pipeline load …`) and a library. FastAPI never imports it, and it never imports pyspark (`pyarrow` is in `requirements-pipeline.txt`, not the API image).

## 3. Source → canonical mapping

Lineage columns on every processed row: `event_id`, `source`, `source_type`, `external_id`, `dedup_key`, `content_hash`, `envelope_schema_version`, `run_id`, `correlation_id`, `ingested_at`, `occurred_at`, `raw_key`, `raw_uri`, `processing_job_id`, `synthetic`, `original_json`.

| Processed column | companies (+entities) | market_bars | news_articles (+sources) |
|---|---|---|---|
| identity | `cik` (UNIQUE) | (`symbol`,`interval`,`trading_date`,`source`) UNIQUE | `sources.url_hash` = sha256(normalize_url(`url`)) UNIQUE |
| `ingested_at` | `source_ingested_at` | `source_ingested_at` | `news_articles.source_ingested_at`, `sources.retrieved_at` |
| `event_id` | `event_id` (FK) | `event_id` (FK) | `news_articles.event_id` (FK) |
| `processing_job_id` | `processing_job_id` (FK) | `processing_job_id` (FK) | `news_articles.processing_job_id` (FK) |
| `content_hash` | `content_hash` (0007) | `content_hash` | `sources.content_hash` |
| `raw_uri` | `raw_uri` (0007) | `raw_uri` | `sources.raw_uri` |
| `source` | `source` (0007) | `source` (part of identity) | `news_articles.source_key` |
| `external_id` | — (CIK is the id) | — | `news_articles.external_id` |
| domain | `name`→`legal_name` (+ entity `canonical_name` on insert), `ticker`, `tickers` (0007), `exchange` | `bar_timestamp`→`bar_ts`, OHLC, `adj_close`, `volume`, `currency` | `sources`: `title`, `url` (normalized), `publisher`, `published_at`; `news_articles`: `author`, `summary`, `tickers` |

Not loaded (they stay in Parquet and raw): `original_json`, `raw_key`, `run_id`, `correlation_id`, `occurred_at`, `source_type`, `envelope_schema_version`, `synthetic`. They are reachable from the canonical row via `event_id → ingestion_records` (run, raw_uri, Kafka position) and the Parquet row.

Company rules: identity is the CIK, never the name. A new CIK whose name is already an entity gets `"<name> (CIK <cik>)"` rather than a merge. Every listed ticker is kept in `tickers`; `ticker` is the first listed one (the `uq_companies_ticker_exchange` constraint applies to it).

## 4. Upsert and version semantics

One rule, `newer_than_existing` (Phase 2.2): a candidate replaces the stored row iff the stored row has no version, or `source_ingested_at` is later, or it is equal and `event_id` is larger. Spark applies the same order within a partition. The loader does not reimplement it anywhere.

| Domain | Statement | Atomicity |
|---|---|---|
| market | `INSERT … ON CONFLICT (symbol, interval, trading_date, source) DO UPDATE … WHERE newer_than_existing RETURNING key` | one statement per chunk; the guard is evaluated under the row lock Postgres takes for ON CONFLICT |
| news | 1. `INSERT INTO sources … ON CONFLICT (url_hash) DO NOTHING`; 2. `INSERT INTO news_articles … ON CONFLICT (source_id) DO UPDATE … WHERE newer_than_existing RETURNING source_id`; 3. update `sources` document fields only for the returned winners | all three in the batch transaction; the article row lock serializes concurrent writers, so both rows always carry the same version |
| company | `SELECT … FOR UPDATE` existing rows by CIK, compare `.version`, insert entity + company or update; one savepoint per chunk, redone with one savepoint per row if a constraint fails | the row lock (Postgres) makes the compare-and-write race-free; a concurrent insert of the same CIK hits `UNIQUE(cik)` |

Outcome classification (counts only, never correctness): the stored `(source_ingested_at, event_id)` of the batch keys is read first in the same transaction. A returned row is *inserted* (key absent) or *updated*. A non-returned row is a *duplicate* (stored `event_id` equals the candidate's) or *stale* (stored version is newer). A row that loses inside the batch is stale (or a duplicate if it is the same event). If the returned rows contradict the pre-read (possible only if a writer bypassed the lock), the batch raises and rolls back instead of reporting wrong numbers.

## 5. Lineage

Source → run → event → raw → job → Parquet → canonical row:

- canonical `event_id` → `ingestion_records` (source, run, `raw_uri`, Kafka topic/partition/offset, status now `loaded`);
- canonical `processing_job_id` → `processing_jobs` (code version, Spark app id, manifest URI, load counts);
- `raw_uri` + `content_hash` on the canonical row (companies since 0007; news on `sources`);
- `processing_jobs.load_report_uri` → the per-attempt load report listing files, partitions, counts and any failed rows.

`processing_job_id` on a canonical row is the job that **first produced** that Parquet row (Step 2.6 keeps a row's original job id when a later job rewrites the partition). The job whose manifest was loaded is on `processing_jobs.load_*`.

## 6. Transaction boundaries

- **One transaction per batch** of ≤ `--batch-size` rows (default 1000) from one Parquet file: canonical upserts for the batch + `ingestion_records → loaded` for the batch's winners, then COMMIT. Never one transaction for the whole load, and a batch never spans files.
- A logical record is never split: a news article's `sources` and `news_articles` rows are in the same batch transaction.
- Lifecycle updates (`CANONICAL_LOAD_RUNNING`, final status) are their own short transactions.
- Committed batches of a failed load stay committed. That is safe because every write is newest-wins and the retry reclassifies them as duplicates.

## 7. Load lifecycle (processing_jobs, migration 0007)

`status` keeps its Step 2.6 meaning (processing only). A separate `load_status` holds the load stage:

```
status=COMPLETED, load_status=NULL        PROCESSING_COMPLETE
load_status=CANONICAL_LOAD_RUNNING        attempt in progress (or crashed: see §8)
load_status=CANONICAL_LOAD_COMPLETE       every candidate inserted/updated/stale/duplicate; invariants hold
load_status=CANONICAL_LOAD_FAILED         validation, constraint, reconciliation or infrastructure failure; load_error says which
```

Columns: `load_status`, `load_attempts`, `load_started_at`, `load_candidates`, `load_inserted`, `load_updated`, `load_stale`, `load_duplicates`, `load_failed`, `load_partitions`, `load_error`, `load_report_uri`. The reserved `loaded_rows` (= inserted + updated), `loaded_at` and `load_duration_ms` are filled too. The row describes the **latest** attempt. Each attempt also writes a write-once report object, so the history is not lost. There is no second tracking table.

Only jobs with `status = COMPLETED` can be loaded; FAILED or RUNNING processing jobs are refused (exit 2).

## 8. Idempotency, retries and partial failure

- **Replay:** loading the same manifest again writes nothing: every row is a duplicate. It is still recorded as a new attempt with its own counts.
- **Crash after commit, before completion:** the job stays `CANONICAL_LOAD_RUNNING`. The advisory lock is session-scoped, so it died with the process. The next attempt takes the lock, logs that it is replacing an unfinished attempt, and reloads: committed rows come back as duplicates.
- **Batch failure** (database error): that batch rolls back, the load is marked FAILED with the error, and the exception propagates (non-zero exit). Re-running is the recovery.
- **Invalid processed rows:** pass 1 finds them before any write; the load fails listing file, row, event id and reason (first 50 in `load_error`, all in the report). Nothing is skipped silently and there is no partial acceptance of invalid rows.
- **Row-level constraint conflicts** (companies only: a ticker+exchange already held by another CIK). The row is reported as failed with its CIK, event id and constraint error. Other rows commit. After all files, failed rows are retried once, because a ticker can move between two CIKs in the same snapshot and the order within a batch is arbitrary. Rows still failing mark the load **FAILED** and are listed in the report. They are not auto-resolved: moving a ticker off another company is a data decision, not a loader decision.
- Exactly-once is **not** claimed. The guarantee is at-least-once delivery into an idempotent, newest-wins sink: re-running any load converges to the same canonical state.

## 9. Manifest-driven selection

| Selector | Meaning |
|---|---|
| `--job <uuid>` | that processing job's manifest |
| `--manifest <key or uri>` | that manifest object (its `job_id` must be a COMPLETED `processing_jobs` row) |
| `--domain <d> --latest` | the most recent COMPLETED processing job of the domain |
| `--since / --until` | optional filter on the manifest's partition dates (inclusive) |

There is no default that loads "everything": with no selector the CLI exits 2. The loader never lists `processed/`. It only opens files named in the manifest and checks each one's size against the manifest. A file that is missing or has a different size means a later processing job rewrote that partition. The load then fails and tells you to load the later job, instead of reading whatever is there now.

## 10. Reconciliation

Per attempt, all enforced; a violation marks the load FAILED:

1. `candidates = inserted + updated + stale + duplicates + failed`.
2. Without a date filter, `candidates = manifest counts.output_rows` (the rows the job left in those files).
3. `rows validated in pass 1 = rows read in pass 2`.
4. `ingestion_records` moved to `loaded` ≤ inserted + updated + duplicates (records already `loaded` don't move again).

## 11. Performance

- Streaming: one Parquet file in memory at a time (a file is capped by Spark's `maxRecordsPerFile`), iterated in record batches; never the whole dataset.
- Market and news: one pre-read per chunk of up to 500 keys, then `INSERT … ON CONFLICT … RETURNING` executed with a list of parameter sets. SQLAlchemy's insertmanyvalues sends that as multi-row statements from one cached compiled statement. A profile of the first version (`insert().values(list)`) showed about a quarter of the load time spent recompiling the multi-row VALUES statement for every batch.
- Companies: a chunk is written under one savepoint and flushed once. Only if a constraint fails is the chunk redone with a savepoint per row, so one bad row never takes the others down.
- COPY into a staging table was **not** used. At current volumes (thousands of rows) it would add a second write path and temp-table management for no measured benefit. It is the documented next step if batch upserts become the bottleneck.

**Measured** (MacBook, Postgres 16 in Docker via `TEST_DATABASE_URL`, synthetic rows written with the real manifest writer, loader in-process, `batch_size` 1000; wall time of `CanonicalLoadRunner.run`):

| Load | First load | Replay (all duplicates) |
|---|---|---|
| market, 10,000 bars in 100 files (100 rows each) | 10.6–11.7 s (≈ 860–940 rows/s; validation ≈ 3.1–3.3 s) | 8.2–10.1 s |
| company, 3,000 CIKs in 1 file | 2.8–3.0 s (≈ 1,000 rows/s) | 1.1–1.2 s |

- Before the two changes above, the same runs took about 14.5 s (market) and 22.5 s (company).
- `batch_size` 5000 changes little for market, because a batch never spans files and these files hold 100 rows. What remains is mostly per-file cost: open, validate, pre-read, upsert, ledger update and commit, about 100 ms per file.
- On the dev compose stack, the real SEC load (5 CIKs) took 1.2 s in the loader and about 8 s including container start.
- These are single-machine numbers for the stated shapes, not a capacity claim.

## 12. Testing

| Tier | What |
|---|---|
| unit (SQLite) | mapping per domain, identity recomputation, validation failures (schema, identifiers, timestamps, domain rules, lineage), outcome classification, reconciliation, selection guards, CLI exit codes; runner end-to-end over Parquet written with pyarrow |
| integration (Postgres) | every domain loader; constraints; per-batch transactions; replay; the conflict matrix (same event twice, newer, older, tie on timestamp, CIK/name conflict, ticker conflict, news and market version conflicts, invalid record, failure mid-load + retry, crash before completion); advisory-lock exclusion |
| spark (container) | Spark process → load → process again → load again: no duplicates, same winners, lineage intact; Spark and canonical agree on tie-breaks |

Tests never touch `backend/data/quantpilot.db`; hash, mtime and Alembic revision are checked before and after the full suite.

## 13. Rollback and recovery

- Code: revert the commit. Migration 0007 has a downgrade that drops the added columns (the data in them is lost; the Step 2.2 columns are untouched).
- Data: the canonical tables are a projection of processed Parquet, which is a projection of the immutable raw lake. To rebuild, truncate the canonical tables and load the latest job of each domain again. Raw and processed objects are never modified by the loader.

## 14. Known limitations (as of this step)

- **No real market or news data in the dev lake.** No Massive API key is configured. The only market bars are `synthetic.market` rows from Step 2.5, which the loader refuses, since synthetic data never enters canonical tables. `make load-market` therefore fails validation with 0 rows written, which is the intended outcome. News was verified with fixtures in the isolated test databases only.
- `--allow-synthetic` works only against a database whose name contains `test`.
- A job whose files a later job rewrote (dynamic partition overwrite) can no longer be loaded; the loader raises `ManifestMismatch` and names the fix, which is to load the later job. Loading the latest job per domain is the supported path.
- Ticker reassignment is not resolved automatically. If a ticker is still held by another CIK after the retry pass, that row fails and the load is marked `CANONICAL_LOAD_FAILED`, with the row listed in the report.
- The loader is not exactly-once. It is idempotent: each batch commits its rows together with their ledger updates, and a rerun converges.

## 15. Out of scope

Neo4j, pgvector, RAG, LangGraph agents, research reports, event-impact analysis, dashboards, Airflow, Spark streaming, COPY staging, automatic ticker-reassignment resolution, and catch-up loading of every unloaded job (load jobs explicitly; `--latest` covers the common case).
