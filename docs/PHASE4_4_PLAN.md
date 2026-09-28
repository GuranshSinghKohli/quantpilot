# QuantPilot — Phase 4.4: Async research execution

Phase 4.4 turns `POST /api/research` into a durable submission. The Phase 4.3 graph is unchanged. The worker calls `run_research()`.

Delivery guarantee: **at-least-once delivery with idempotent processing**.

## Flow

```
POST /api/research
  → validate request and size ceilings
  → insert research_jobs (publish_pending) and research_outbox in one transaction
  → return research_id, status, correlation_id, status_url
research worker
  → publish pending outbox rows to Kafka
  → on ack, status becomes queued
  → consume quantpilot.research.request (group quantpilot.research-worker)
  → lease the job, call run_research(), persist, commit the DB, then commit the offset
GET /api/research/{research_id}
  → durable status, plan, report, claims, agent runs
```

Kafka messages carry identifiers only: `research_id`, `idempotency_key`, `request_version`, status, correlation id. They do not carry the EvidencePackage, documents, or credentials. The worker loads the request from PostgreSQL.

The Kafka key is `research_id`. Ordering is per partition, not global.

## Topics

Created by `kafka init` (auto-create stays off):

| Topic | Default | Partitions |
|---|---|---|
| `quantpilot.research.request` | `KAFKA_TOPIC_RESEARCH_REQUEST` | `KAFKA_RESEARCH_PARTITIONS` (3) |
| `quantpilot.research.completed` | `KAFKA_TOPIC_RESEARCH_COMPLETED` | same |
| `quantpilot.research.dlq` | `KAFKA_TOPIC_RESEARCH_DLQ` | 1 |

Replication factor and retention follow `KAFKA_REPLICATION_FACTOR`, `KAFKA_RESEARCH_RETENTION_HOURS`, and `KAFKA_DLQ_RETENTION_HOURS`.

## Outbox

`research_outbox` rows are `pending`, `published`, or `failed`. Publishers claim a batch (`FOR UPDATE SKIP LOCKED` on PostgreSQL). A crash after the broker ack and before the published mark can deliver a duplicate. The consumer treats a terminal job as already done and does not insert another claim set.

If the request event cannot be published, the job stays `publish_pending` or becomes `failed` after `RESEARCH_MAX_ATTEMPTS`. The API does not report `queued` until the request event is marked published.

Completion and DLQ rows use the same outbox. A completion publish failure leaves the job status as it was.

## Lease, retry, DLQ

`worker_id`, `lease_until`, and `heartbeat_at` live on `research_jobs`. The worker commits that lease before `run_research()`, then renews it from a second database session every `RESEARCH_HEARTBEAT_SECONDS`. A second worker does not take a live lease. PostgreSQL takes a row lock while claiming. A lease older than `RESEARCH_LEASE_SECONDS` can be reclaimed by the same loop. Ownership is a PostgreSQL row update, not a separate lock service.

Retryable failures (timeouts, transient provider or database errors) set the job back to `queued` with `next_attempt_at`. Non-retryable failures (`auth`, malformed input, oversized requests) fail immediately. After `RESEARCH_MAX_ATTEMPTS` the job is `failed` and a DLQ outbox row is written. There is no automatic DLQ replay.

`partial` stays `partial`. A report does not promote it to `succeeded`.

## Statuses

`publish_pending`, `queued`, `running`, `succeeded`, `partial`, `failed`, `cancelled`.

`POST` returns 202. The same idempotency key returns the existing job and does not enqueue a second request. A full queue (`RESEARCH_MAX_QUEUED_JOBS`, counted from PostgreSQL) returns 429. Oversized requests return 400.

## Local process

```
docker compose --profile pipeline --profile research up -d research-worker
```

The service sets `KAFKA_BOOTSTRAP_SERVERS=kafka:29092`. `LLM_PROVIDER` defaults to `fake` in that compose service. `python -m app.research worker` is the same loop: outbox publish, stale-job reclaim, then consume.

## Limits

No webhooks. No automatic DLQ replay. No claim of throughput. Fixture timings are not capacity. FakeLLM evaluation remains development-only and does not measure this queue.
