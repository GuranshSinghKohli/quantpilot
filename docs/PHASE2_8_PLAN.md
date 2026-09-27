# Phase 2 · Step 2.8 — Data platform hardening + observability

Operational layer around the path that already exists:

```
External Sources → Adapters → Kafka → Raw Lake → PySpark → Processed Parquet → Canonical PostgreSQL
```

This step does not add sources, intelligence, or dashboards. It makes the existing
platform observable, measurable, recoverable, and safe to rerun.

## 1. Current observability gaps (inspected before writing code)

| Piece | State before 2.8 | Gap |
|---|---|---|
| `app/pipeline/metrics.py` | In-process counters/gauges/summaries + `render_prometheus()` | No `/metrics`, no label allowlist, no cardinality cap; names differ by component |
| Ingest / HTTP / Kafka / lake-writer / Spark | Already increment process-local metrics | Names are not the Step 2.8 set; canonical loader emits none |
| `GET /health` | Process up + cheap DB/Redis flags (Phase 12) | Correctly does **not** call Kafka, MinIO, or providers. Keep it that way |
| `GET /ready` | DB + Alembic revision + Redis + draining | Does not *report* Kafka or lake. Must not *require* them (API works without the pipeline) |
| Structured logs | JSON + secret redaction + `request_id` | Pipeline fields (`component`, `operation`, `domain`, `correlation_id`) are ad-hoc |
| `correlation_id` | On the event envelope, Kafka header, processed Parquet | Job-level Spark/load logs do not bind it; no documented propagation |
| CLI | `lake`, `kafka`, `dlq`, `ingest`, `process`, `load`, `sources` | No status, stale-job report, reconciliation, or guarded retry |
| DLQ | `make dlq` lists messages, payload hidden unless `--show-payload` | No aggregate (count / component / reason / first-last) |
| Compose | No Prometheus | Target stack listed Grafana/Prometheus as unshipped |
| Retention / backup | Kafka retention settings exist; Phase 12 Postgres dump notes | No pipeline retention or lake/canonical recovery procedure |

## 2. Metrics architecture

One registry: the existing `Metrics` class. No Prometheus client library. The
application starts if Prometheus is down; `/metrics` is best-effort.

**Process-local counters** (ingest, lake-writer, Spark, loader, API) use the
names below. **API `/metrics`** also exports low-cardinality gauges read from
Postgres (record/job counts by domain and status) so a scrape of the API can
answer "what is the platform doing" even though CLI jobs are one-shot.

Label rule (enforced in `Metrics`): only `domain`, `source`, `operation`,
`status`, `component`, `topic`, `partition`, `result`, `kind`, `provider`,
`reason`, `method`, `route`. Forbidden as labels: `event_id`, `job_id`,
`request_id`, Kafka offset, URL, ticker, CIK, name, title, raw error, user id.
Those belong in structured logs. A metric may have at most 64 series.

| Family | Names |
|---|---|
| Ingestion | `ingestion_runs_total`, `ingestion_records_total`, `ingestion_records_published_total`, `ingestion_records_duplicate_total`, `ingestion_records_failed_total`, `ingestion_duration_seconds`, `source_request_duration_seconds`, `source_request_failures_total`, `source_rate_limit_total` |
| Kafka | `kafka_messages_published_total`, `kafka_publish_failures_total`, `kafka_messages_consumed_total`, `kafka_processing_failures_total`, `kafka_dlq_messages_total`, `kafka_consumer_lag` |
| Lake | `lake_writes_total`, `lake_duplicate_writes_total`, `lake_write_failures_total`, `lake_read_failures_total`, `lake_bytes_written` |
| Spark | `processing_jobs_total`, `processing_duration_seconds`, `processing_input_rows`, `processing_output_rows`, `processing_rejected_rows`, `processing_duplicate_rows`, `processing_failures_total` |
| Canonical | `canonical_loads_total`, `canonical_rows_inserted`, `canonical_rows_updated`, `canonical_rows_stale`, `canonical_rows_duplicate`, `canonical_rows_failed`, `canonical_load_duration_seconds` |
| API | `http_requests_total`, `http_request_duration_seconds` (route family, not ticker/id paths) |

A few existing extras stay (`source_requests_total`, publish/write retries).
No new high-cardinality series.

## 3. Health / readiness architecture

- **`/health`**: "is this process alive?" Keeps the existing cheap DB/Redis flags
  (Phase 12 tests and clients depend on them). Does **not** call Kafka, MinIO,
  Massive, or SEC.
- **`/ready`**: fails only when Postgres is down, migrations are behind, or the
  process is draining. **Reports** `kafka` and `lake` (`ok` / `unavailable` /
  `unconfigured`) without failing readiness when the pipeline is off.
- Provider health stays on `make sources-check`.

## 4. Pipeline lifecycle

Existing state machines are reused. No second one.

```
ingestion_runs:    PENDING → RUNNING → COMPLETED | PARTIAL | FAILED
ingestion_records: pending → published | publish_failed | dlq → landed → processed → loaded
                   (RECORD_TRANSITIONS; never backwards)
processing_jobs:   RUNNING → COMPLETED | FAILED
load_status:       NULL (PROCESSING_COMPLETE) → CANONICAL_LOAD_RUNNING
                   → CANONICAL_LOAD_COMPLETE | CANONICAL_LOAD_FAILED
```

`correlation_id` is the existing envelope field:

1. Adapter run starts → `correlation_id = run_id` (already `build_event`).
2. Kafka header `correlation_id` (already `serde`).
3. Lake writer logs the event's `correlation_id`.
4. Spark copies it into processed Parquet (already in the schema).
5. Loader job-level logs bind `processing_job_id`; per-row lineage keeps the event `correlation_id`.
6. If a CLI operation has no event, it generates one UUID at start and uses that.

## 5. Failure recovery

Detection (`make pipeline-check`) only. Stuck `RUNNING` / `CANONICAL_LOAD_RUNNING`
past `PIPELINE_STALE_*_SECONDS` (default 3600) are **reported**, never auto-failed.

| Command | Prerequisite | Action |
|---|---|---|
| `retry-load JOB` | Job exists, processing `COMPLETED`, manifest present and sizes match, schema supported | Existing `CanonicalLoadRunner` |
| `reprocess-job JOB` | Job exists and is not `RUNNING`, manifest `input.objects` still in the lake | Existing `ProcessingJobRunner` on those keys |
| `retry-ingestion RUN` | Run exists, `FAILED` or `PARTIAL`, source still configured | New ingest with the stored window/symbols |

No automatic DLQ replay. `make dlq` remains read-only unless `--show-payload`.

**Safe recovery of a stale RUNNING job:** confirm the process is gone (no Spark
driver, no load container). Then `retry-load` / `reprocess-job`. The load lock
and processing lock refuse a second live writer.

## 6. Data reconciliation

`python -m app.pipeline reconcile` is scoped by `--domain`, `--job`, and/or
`--since`/`--until`. It never lists the whole lake.

Checks:

1. Landed (and later) records have `raw_uri`.
2. Processed/loaded records reference a real `processing_jobs` row.
3. Canonical rows' `event_id` / `processing_job_id` / `raw_uri` exist.
4. The job manifest's files exist with the recorded size (that job only).
5. `CANONICAL_LOAD_COMPLETE` counts: candidates = inserted+updated+stale+duplicates+failed.
6. State invariants (below).
7. No canonical lineage pointing at a missing record or job.

## 7. Operational commands

| Command | Purpose |
|---|---|
| `make pipeline-status` | Latest run/job/load per domain, failed/running, DLQ summary, Kafka lag, last success |
| `make pipeline-check` | Stale runs/jobs/loads (report only) |
| `make pipeline-reconcile` | Scoped invariants |
| `make pipeline-quality` | Per-domain count chain |
| `make dlq` | DLQ inspect (now with `--summary`) |
| `make kafka-check` / `lake-check` | Unchanged |
| `make retry-load JOB=…` etc. | Guarded recovery |

## 8. Alert-worthy conditions

These are conditions an operator (or a later Prometheus rule) should notice.
This step does **not** ship alertmanager or Grafana.

- `/ready` `status=degraded` (DB or migrations).
- Ingestion run `FAILED`, or `PARTIAL` with `publish_failed > 0`.
- Processing job or canonical load `FAILED`.
- Stale `RUNNING` / `CANONICAL_LOAD_RUNNING`.
- Kafka consumer lag growing on a raw topic.
- DLQ count increasing.
- Reconciliation violations.
- Source `429` / auth failures (`make sources-check`), not `/ready`.

## 9. Data retention

Nothing is deleted automatically. Raw objects stay write-once.

| Store | Recommendation | Why |
|---|---|---|
| Kafka raw topics | `KAFKA_RAW_RETENTION_HOURS` (default 168 = 7d) | Transport, not the system of record |
| Kafka DLQ | `KAFKA_DLQ_RETENTION_HOURS` (default 720 = 30d) | Enough to inspect; payloads are also in logs/tracking |
| Raw lake | Keep | System of record for what the provider returned |
| Processed lake | Keep; later jobs may overwrite partitions | Loader reads the latest COMPLETED job |
| Rejects | Keep (write-once per event) | Audit of failed checks |
| Manifests / load reports | Keep (write-once) | Job identity |
| Postgres | Logical dump (see §17) | Canonical + tracking |

No cleanup command is shipped. If one is added later it must require an explicit
scope, print the keys, refuse paths outside the lake prefix, and never default
to raw/.

## 10. Known limitations

- CLI jobs are one-shot: their process-local counters live for that process.
  `/metrics` on the API exposes HTTP metrics plus DB-derived gauges, not a live
  Spark driver's in-memory counters.
- `/ready` does not fail when Kafka or MinIO is down (the API does not need them).
- Stale-job detection does not kill or fail jobs.
- DLQ replay is not implemented.
- No Grafana. Prometheus is an optional compose profile.
- Massive/SEC outages are visible via `sources-check` and ingest metrics, not readiness.
- Exactly-once is not claimed (same as 2.4–2.7): at-least-once + idempotent writes.

## 11. Testing and rollback

Unit + integration: metrics increment and reject bad labels; `/health` independent
of Kafka/lake; `/ready` reports them; status/check/reconcile/recovery/DLQ; the
dev SQLite fingerprint is unchanged by tests.

Rollback: revert the commit. No migration. Optional Prometheus container is unused
if the profile is off.
