# QuantPilot — Phase 2 Step 2.6: PySpark Processing (Raw Lake → Processed Parquet)

Baseline: `e80ae48 feat: add external source ingestion adapters`. Steps 2.4–2.5 land every event as one write-once JSON object in the raw lake and track it in `ingestion_records` (`landed`). Step 2.6 adds the batch layer that turns those raw objects into typed, validated, deduplicated Parquet. **Nothing is loaded into canonical Postgres tables yet** (Step 2.7).

```
raw/<domain>/year=/month=/day=/<event_id>.json      (write-once, never modified)
      │  plan_inputs: ObjectStore.list per requested day (or prefix / explicit keys) → exact object list
      ▼
Spark reader: explicit wire schema, PERMISSIVE + multiLine, _metadata.file_path → raw_key
      │  validate: envelope rules + domain rules → first failing check (reject_code, reject_message)
      ├──► rejects/<domain>/year=/month=/day=/<event_id>.json   (write-once, via ObjectStore)
      ▼
normalize (typed columns + lineage) → drop exact duplicates (event_id)
      │  merge with the rows already in the affected partitions (existing row wins on event_id)
      │  keep newest version per dedup_key → localCheckpoint
      ▼
processed/<domain>/year=/month=/day=/part-*.parquet   (dynamic partition overwrite)
      │
      ├──► _manifests/processing/<job_id>.json            (write-once)
      ├──► processing_jobs row (RUNNING → COMPLETED | FAILED)
      └──► ingestion_records landed → processed (+ processing_job_id) for rows this job added
```

## 1. What already existed and is reused

| Piece | From | Used for |
|---|---|---|
| `ObjectStore` (`LocalObjectStore`, `S3ObjectStore`), `put_if_absent`, key helpers | 2.3 | Input listing, reject objects, manifests, output listing. Spark reads/writes Parquet by URI (see §3) |
| Contracts (`NewsRecord`, `MarketRecord`, `CompanyRecord`, `IngestionEvent`, `SOURCES`, `SYMBOL_PATTERN`) | 2.2–2.5 | Wire schema field lists (checked by a unit test), source registry, symbol regex. Identity (`event_id`, `dedup_key`, `content_hash`) is **never re-derived** in Spark; it is only checked |
| `processing_jobs`, `start_processing_job`, `finish_processing_job`, `advance_records`, `lineage()` | 2.2 | The only job tracker. Migration `0006` adds four columns to it (§8) |
| Structured logging, `Metrics` | 1, 2.4 | `pipeline.spark.job_*` events, `processing_*` counters |
| MinIO in compose, pinned credentials pattern | 2.3 | S3A endpoint and AWS_* variables |

No source-adapter logic is duplicated: Spark reads what the adapters already normalized and only re-validates it.

## 2. Package layout and isolation

`backend/app/pipeline/spark/`:

| Module | JVM? | Role |
|---|---|---|
| `config.py` | no | `SparkConfig` from Settings → Spark conf dict (UTC, dynamic overwrite, S3A endpoint; **no credentials**) |
| `plan.py` | no | Input selection, caps, Spark paths/URIs |
| `schemas.py` | no (lazy builders) | Wire and processed column specs, partition basis, sort keys |
| `quality.py` | no (lazy) | Documented check codes, `Rule`, `first_failure` |
| `manifest.py`, `writers.py` (rejects, output listing) | no | Write-once JSON via ObjectStore |
| `jobs/runner.py`, `jobs/lock.py` | no | Lifecycle: lock → job row → engine → rejects → manifest → lineage → finish |
| `session.py`, `readers.py`, `transformers/`, `dedup.py`, `jobs/engine.py` | yes | Spark itself |

- The API never imports this package; a unit test imports `app.main` in a subprocess and asserts neither `pyspark` nor `app.pipeline.spark` is loaded.
- The SparkSession is created lazily by the engine, only from `python -m app.pipeline process …` or the spark test tier.
- `pyspark` is in `requirements-spark.txt`, installed only in the `spark` / `spark-test` images. On a host without pyspark the CLI exits 2 with instructions.

## 3. Runtime and S3A

- **Versions:** PySpark **4.1.3** (bundles Hadoop client 3.4.2), Java **17** (Temurin JRE copied from a digest-pinned image), `hadoop-aws` **3.4.2**, AWS SDK v2 `bundle` **2.29.52**, `analyticsaccelerator-s3` **1.2.1**. The three jars are fetched with `ADD --checksum=sha256:…` into `pyspark/jars` at image build (SHA-1s cross-checked against Maven Central). Spark 3.5 was rejected because its Hadoop 3.3 S3A uses the end-of-life AWS SDK v1.
- **Dockerfile stages:** `deps` → `jre` → `spark-base` → `spark` (job image) and `spark-test` (+ dev requirements; backend mounted by compose); `app` stays the last, default stage, so the API/migrate/pipeline images are unchanged.
- **Master:** `SPARK_MASTER=local[*]` (validated: `local`, `local[N]`, `local[*]`, `spark://`, `k8s://`, `yarn`). Only `local[*]` is used or tested. Nothing here claims distributed scalability.
- **S3A config:** endpoint, path-style access and TLS flag from `LAKE_ENDPOINT_URL`, region from `LAKE_REGION`. **Credentials never go into the Spark conf** (it appears in logs and event logs): S3A's default provider chain reads the same `AWS_*` variables boto3 uses. A unit test asserts no credential keys or values in the conf. With `LAKE_BACKEND=s3` the session fails fast (exit 2 message) if the S3A classes are missing.
- **Why Spark does not go through `ObjectStore` for data:** routing every raw object through the Python store would funnel all bytes through the driver and defeat the engine. Spark reads the exact object list planned via `ObjectStore.list`, and writes Parquet with its own committer. Everything that must be write-once (rejects, manifests) goes through `ObjectStore.put_if_absent`. Spark never writes under `raw/`.

## 4. Input selection

`plan_inputs(store, request)` chooses exactly one of:

| Mode | CLI | Listing |
|---|---|---|
| Ingestion-date window (default: today UTC) | `--start/--end` (inclusive) | one `store.list("raw/<domain>/year=…/day=…/")` per day |
| Prefix | `--prefix raw/<domain>/…` | one list call; must be under `raw/<domain>/` |
| Explicit objects | `--objects k1,k2` / `--objects-file` | each key parsed, domain-checked, `exists()`-checked |

Caps: `PROCESS_MAX_WINDOW_DAYS` (366) and `PROCESS_MAX_INPUT_OBJECTS` (100 000). There is no "whole lake" default. Non-event keys found under a selection are listed as `ignored` in the manifest. `--output-prefix` must be a folder under `processed/`. Planning errors exit with code 2 before any job row exists.

## 5. Schemas (explicit; no inference)

- **Wire schema** (`schemas.WIRE_RECORD_FIELDS`, `ENVELOPE_FIELDS`): the raw JSON exactly as the lake writer stores it. Every scalar is read as **string**, tickers as `array<string>`, `original` as JSON text. A value of the wrong type therefore reaches the checks and gets a specific reject code instead of becoming a silent null. Unit tests assert the field sets equal the pydantic contract models.
- **Reader:** `spark.read.schema(wire).option(mode=PERMISSIVE, multiLine=true, columnNameOfCorruptRecord=_corrupt_record)`. `raw_key` comes from `_metadata.file_path`. Objects that contain several documents (a top-level array) or none (empty) are detected explicitly.
- **Processed schema** (schema version 1): 16 lineage columns + domain columns; partitions `year/month/day` (strings) live in the path.

| Lineage columns (all domains) |
|---|
| `event_id`, `source`, `source_type`, `external_id`, `dedup_key`, `content_hash`, `envelope_schema_version`, `run_id`, `correlation_id`, `ingested_at`, `occurred_at`, `raw_key`, `raw_uri`, `processing_job_id`, `synthetic`, `original_json` |

| Domain | Columns |
|---|---|
| news | `title`, `url`, `published_at` (timestamp), `author`, `publisher`, `summary`, `tickers` (array) |
| market | `symbol`, `interval`, `trading_date` (date), `bar_timestamp`, `open/high/low/close/adj_close` (decimal(20,6)), `volume` (bigint), `currency` |
| company | `cik` (10-digit string), `name`, `ticker`, `tickers` (array), `exchange` |

## 6. Transformations

Native Spark SQL expressions only: no Python UDFs, no row loops, no `collect` of data. Raw objects are only read.

- **All:** timestamps must be ISO 8601 with an explicit offset and are stored as UTC instants (session time zone UTC, JVM `-Duser.timezone=UTC`). `raw_uri` equals `ObjectStore.uri(raw_key)`. `synthetic` is true for sources registered as synthetic.
- **News:** Unicode whitespace in text collapsed and trimmed (empty → null); tickers upper-cased, trimmed, deduplicated in order. The URL is kept as the source gave it (the contract's `dedup_key` already holds its normalized hash). No field is invented: missing author/summary stay null.
- **Market:** prices parsed from plain decimal strings to `decimal(20,6)` (no float), volume to bigint. `trading_date` is the America/New_York session date the adapter derived; it is **parsed, never recomputed** from the UTC timestamp. `adj_close` and `currency` stay null when the source did not supply them.
- **Company:** CIK is the identity (10 digits, must equal both external ids). Names are never used to merge. Every listed ticker is kept in `tickers`; `ticker` must be its first entry; missing ticker/exchange stay null.

## 7. Data-quality rules and rejects

Each row gets the **first** failing check (ordered list of `Rule(code, failed, message)` → `reject_code`, `reject_message`). All codes are documented in `quality.CHECKS` and repeated in each manifest.

| Scope | Checks (in order) |
|---|---|
| Object / envelope | `malformed_json`, `unreadable_object` (empty), `multiple_documents`, `missing_required_field`, `unsupported_schema_version`, `domain_mismatch`, `invalid_identifier`, `event_id_key_mismatch`, `unknown_source`, `source_type_mismatch`, `record_envelope_mismatch`, `identity_mismatch` (event_id ≠ sha256(source\|external_id\|content_hash)), `invalid_timestamp`, `timestamp_out_of_range` (before 1970 or > job start + 1 day) |
| News | missing title/url/published_at/external_id, `identity_mismatch`, `invalid_text` (lengths), `invalid_url` (absolute http(s)), `invalid_timestamp`, `timestamp_out_of_range`, `invalid_symbol` |
| Market | missing fields, `invalid_symbol`, `invalid_interval` (1d), `invalid_timestamp`, `timestamp_out_of_range`, `invalid_numeric`, `non_positive_price` (> 0, < 1e14; adj_close too), `invalid_volume` (whole, ≥ 0), `high_below_low`, `ohlc_inconsistent` (open, close ∈ [low, high]), `trading_date_mismatch` (> 1 day from the bar timestamp), `identity_mismatch` (external_id = symbol:interval:date), `invalid_currency` |
| Company | missing fields, `invalid_cik`, `identity_mismatch`, `invalid_text`, `invalid_symbol`, `ticker_mismatch` |

**Reject objects:** `rejects/<domain>/year=/month=/day=/<event_id>.json` (the raw object's date and id, so one reject per raw object), written with `put_if_absent`: an existing reject is **kept, never overwritten**, and counted as `already_recorded`. Fields: `event_id`, `envelope_event_id`, `source`, `domain`, `processing_job_id`, `error_code`, `error_message`, `check`, `raw_key`, `raw_uri`, `processed_at`, `reject_version`. Rejected records stay `landed` in `ingestion_records` (there is no `rejected` status; the reject object and manifest are the record). Nothing is silently dropped: `input_objects = valid_rows + rejected_rows` is enforced.

## 8. Deduplication and reprocessing

1. **Exact duplicates:** the same `event_id` in several objects (e.g. a copy under another day) → one row, lowest `raw_key` wins.
2. **Already processed:** the job reads the rows already in the partitions it will rewrite (explicit processed schema). A new row whose `event_id` is already there is dropped in favour of the **existing** row, which keeps its original `processing_job_id`. Rerunning a window is therefore a logical no-op.
3. **Superseded versions:** per contract `dedup_key` (news: URL hash; market: symbol + interval + trading_date + source; company: CIK), only the newest `ingested_at` survives, tie-broken by `event_id` ascending. Titles and names never key a merge.

Then `localCheckpoint(eager=True)` materializes the merged result, so the overwrite never reads the files it replaces, and the partitions are written with **dynamic partition overwrite** (only partitions in the output are replaced, each with its complete merged contents). Counters: `input_objects`, `valid_rows`, `rejected_rows`, `duplicate_rows`, `already_processed_rows`, `existing_rows`, `superseded_rows`, `output_rows`, `new_rows`; invariants are checked before the job may complete (`output = valid − duplicate − already_processed + existing − superseded`).

**Determinism:** the same raw input gives the same rows, lineage included (verified: repeated runs → identical rows; §16). It is *logical*, not byte-level: Parquet file names contain a write UUID and files are rewritten. **Rebuilding** with new logic: write to a new `--output-prefix`, or delete the processed partitions first. Raw write-once is untouched by any of this.

**Concurrency:** one job per output prefix. On Postgres the runner holds a session-level advisory lock keyed by the output prefix for the whole job (released by the database if the process dies); a second job exits with a clear error before creating a job row. SQLite takes no lock.

## 9. Partitioning

`processed/<domain>/year=YYYY/month=MM/day=DD/` — the same shape as raw, one level only, no symbol/source partitions (at this volume they would create thousands of tiny files).

| Domain | Partition date | Sort within file |
|---|---|---|
| news | `published_at` (UTC date) | `published_at, event_id` |
| market | `trading_date` (exchange session date) | `symbol, trading_date, source, event_id` |
| company | `ingested_at` date (one reference snapshot per ingestion day) | `cik, event_id` |

One file per partition (`repartition(year, month, day)`; `spark.sql.files.maxRecordsPerFile` splits huge partitions). Partition-type inference is off, so values stay zero-padded strings.

## 10. Job lifecycle, tracking, manifests, lineage

1. Advisory lock on the output prefix (Postgres).
2. `processing_jobs` row committed `RUNNING` (`job_name spark.process.<domain>`, `mode incremental`, `since/until` or `input_prefix`, `input_objects`, `engine_version "spark 4.1.3"`, `code_version` = git short SHA from `make`).
3. Engine runs (skipped entirely for an empty input: no Spark session).
4. Rejects written, output files listed, invariants checked.
5. Manifest `_manifests/processing/<job_id>.json` written once: job, status, times, duration, code version, engine (name, version, master, Spark app id), store, input (mode, window/prefix, every object, ignored keys), output (uri, partitions, files with size, partition basis, sort keys), rejects (counts, by code with descriptions, keys), counts, schema (processed version, envelope versions, column specs).
6. `ingestion_records` of rows this job added → `processed` + `processing_job_id`.
7. Job row `COMPLETED` with counts, partitions, `output_uri`, `manifest_uri`, `spark_app_id`, duration.

**Failure:** any exception → best-effort `FAILED` manifest (with the error), job row `FAILED` with the error, `pipeline.spark.job_failed` log, exception re-raised, CLI exit 1. A failed job is never reported as completed and records are not advanced. A failure *during* the Parquet write can leave rewritten partitions; rerunning the same selection repairs them (merge-on-read is idempotent). Retry = rerun.

**Lineage:** raw object (`raw_key`, `raw_uri`) → job (`processing_job_id`, manifest) → processed row (`event_id`, `source`, `content_hash` on every row) → `ingestion_records.processing_job_id`, and `lineage(event_id)` includes the job's `spark_app_id`.

**Observability:** `pipeline.spark.job_started|job_finished|job_failed` with job id, Spark app id, domain, counts, rejects by code, duration, failure reason; metrics `processing_jobs_total`, `processing_job_duration_seconds`, `processing_rows_total{kind}`, `processing_rejects_total{code}`; the CLI prints them as JSON.

## 11. Commands

```
make spark-build                                     # spark + spark-test images
make pipeline-up                                     # postgres, migrate, MinIO, Kafka, lake writer
make spark-market PROCESS_START=2026-09-01 PROCESS_END=2026-09-27
make spark-news PROCESS_PREFIX=raw/news/year=2026/month=09/
make spark-company PROCESS_OBJECTS=raw/company/year=…/<event_id>.json PROCESS_OUTPUT_PREFIX=processed/company-rebuild/
make spark-all [PROCESS_START= PROCESS_END=]         # news, market, company; default today UTC
make test-spark                                      # spark tier in the spark-test image (MinIO buckets are throwaway)
```

Direct: `python -m app.pipeline process {news|market|company|all} [--start --end | --prefix | --objects | --objects-file] [--output-prefix]`. Exit 0 = every job COMPLETED, 1 = a job failed, 2 = bad input/configuration.

Settings (see `docs/ENV_VARIABLES.md`): `SPARK_MASTER`, `SPARK_APP_NAME`, `SPARK_SHUFFLE_PARTITIONS` (8), `SPARK_LOG_LEVEL`, `SPARK_DRIVER_MEMORY` (2g), `SPARK_MAX_RECORDS_PER_FILE`, `PROCESS_MAX_WINDOW_DAYS`, `PROCESS_MAX_INPUT_OBJECTS`, `CODE_VERSION`.

## 12. Testing

| Tier | Where | Covers |
|---|---|---|
| unit (`tests/unit/pipeline/test_processing_pure.py`) | host, no JVM | conf (UTC, no credentials, S3A only for s3), settings validation, planning (days listed, caps, modes, bad selections, URIs), schema specs = contracts, every rule code documented, no UDF/inference in the package, runner lifecycle with a fake engine (COMPLETED, FAILED, empty input, invariant violation, release on failure, write-once manifest), API import isolation, CLI exit 2 |
| integration (`test_processing_lock.py`) | Postgres | advisory lock refuses a second writer, is per prefix, released on failure |
| spark (`tests/spark/`) | `spark-test` image, real PySpark `local[2]`, tmp local lake + tmp SQLite | raw → processed for each domain; explicit schema; unknown fields ignored; malformed / array / empty objects; 13 news, 15 market, 5 company checks; null adj_close/currency kept; CIK identity + all tickers; reject layout + write-once; duplicate event; versions by dedup_key (not title); market per source; partition layout; reprocessing identical; incremental merge; multiple days and sources; tracking + manifest + record lineage; empty input; Spark failure → FAILED |
| spark + s3 (`test_processing_minio.py`) | MinIO through S3A, throwaway bucket | all three domains twice: identical rows, `s3://` lineage, manifests, job rows, reject, raw hashes unchanged |

## 13. Performance notes

Measured, not extrapolated. On this laptop (Docker Desktop, `local[*]`, MinIO through S3A), over the 5 raw objects per domain in the dev lake: market (5 output partitions) takes 39–43 s per job and company (1 partition) 8–12 s, first run or rerun alike; an empty news window takes < 0.3 s because no Spark session starts. The time is fixed overhead, not data: S3A's copy-based commit per partition, per-partition listings, and about a dozen small Spark actions per job (counts, partition list, reject and new-id streams). The spark tier (54 tests, local lake + one MinIO test) takes ~6 min for the same reason. Design choices for larger inputs: day-partition listing (no lake scans), explicit schemas (no inference pass), native expressions only, `spark.sql.shuffle.partitions` configurable (default 8), one repartition by the partition columns before the write, `toLocalIterator` for the only driver-side streams (reject rows and new event ids), and `localCheckpoint` instead of re-reading. No throughput claims are made; nothing has been run beyond `local[*]`.

## 14. Limitations

- `local[*]` only; no cluster, no Structured Streaming, no Delta/Iceberg, no Airflow.
- Merge-on-read reads each affected partition in full; fine at day granularity, a table format would be needed for very large partitions.
- News versions whose `published_at` changed can land in different partitions and are not collapsed across partitions (dedup is per partition). Company is a per-day snapshot by design.
- Reject writing and record advancement happen on the driver; they are bounded by the number of rejects/new rows, not by data volume.
- S3A creates zero-byte directory marker objects under `processed/`; they are ignored by listings.
- SQLite runs take no concurrency lock.
- No canonical Postgres loading (Step 2.7), no Neo4j, RAG, agents or analytics use processed data yet.

## 15. Out of scope

PostgreSQL canonical loading, Neo4j, RAG, agents, research reports, frontend analytics, event-impact analysis.

## 16. As built and verification (2026-09-27)

- **Tests:** in the `spark-test` image with every tier enabled (Postgres, MinIO, Kafka, Spark): **764 passed, 0 skipped**. On the host (no Java): 710 passed, 54 skipped (exactly the spark tier).
- **Live MinIO run** on the dev lake's existing raw data (5 SEC company objects from the Step 2.5 live ingest, 5 SYNTHETIC market events; no news): `make spark-all` five times plus one extra `spark-market` (16 job rows). First run: market 5 rows in 5 trading-date partitions, company 5 rows in 1 snapshot partition, 0 rejects, news window empty (no Spark session). Every later run: `already_processed = 5`, `new_rows = 0`, and every processed row still carries the first run's `processing_job_id`.
- **Raw immutability:** sha256 of all 10 raw objects identical before and after.
- **Tracking:** all `processing_jobs` rows `COMPLETED` with counts, `spark_app_id`, `engine_version spark 4.1.3`, `code_version`, manifest URI; all 10 `ingestion_records` `processed` with the first job's id. A concurrent second `spark-market` was refused by the advisory lock and created no job row.
- **Rejects and failure paths** were verified by the spark tier (local lake and MinIO), since the dev lake has no malformed data and none was injected into it.
