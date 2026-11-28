# Phase 2 · Step 2.4 — Kafka event backbone + lake writer

Status: **implemented** (this file is the plan and the as-built record).
It narrows `PHASE2_PLAN.md` §4, §10 and §11 to what this step ships. Nothing here adds source adapters (Step 2.5) or Spark (Step 2.6).

```
 (synthetic publisher in 2.4, real adapters in 2.5)
        │  IngestionEvent (validated, event_id derived from content)
        ▼
 EventPublisher ── claim_event ──► ingestion_records: pending
        │  EventProducer.publish_many  (acks=all, idempotent producer, key = event_id)
        ▼
 Kafka: quantpilot.{news,market,company}.raw ──────────────┐
        │  group quantpilot.lake-writer, manual commits    │  (future consumers get their own groups)
        ▼                                                  │
 LakeWriter: decode → validate → key/topic check → lineage check
        │  ObjectStore.put_if_absent(raw_key_for(event), canonical bytes)
        ▼
 raw/<domain>/year=/month=/day=/<event_id>.json  (write-once; MinIO/S3 or local)
        │  record_landing → ingestion_records: landed + raw_uri + topic/partition/offset
        ▼
 commit offset (synchronous, only after the lake write and the DB commit)

 any permanent failure / retries exhausted ─► quantpilot.ingestion.dlq (acked) ─► record → dlq ─► commit offset
```

Kafka is the transport. The lake is the durable record of raw data. Postgres holds the tracking state and, later, the canonical rows.

## 1. What is reused

| Existing piece | Used for |
|---|---|
| `contracts.IngestionEvent`, `build_event`, `serde.encode_event/decode_event/event_headers`, `validation.check_event` | Message envelope and its validation. The only change is one additive optional field, `correlation_id` |
| `dlq.dead_letter`, `contracts.DeadLetter` | The DLQ value. Additive: an optional caller-supplied `dlq_id` so redeliveries produce the same ID |
| `retry.RetryPolicy`, `call_with_retry`, `is_transient` | Every retry loop (producer, lake writer, DLQ publish) |
| `repositories.ingestion.claim_event`, `record_deliveries`, `advance_records`, `RECORD_TRANSITIONS` | Event state. Additive: `record_landing`, which also stores the Kafka position |
| `tracking.RunTracker` | Run counters for published / duplicate / publish_failed |
| `lake.build_object_store`, `ObjectStore.put_if_absent`, `keys.raw_key_for` | The raw write. `ObjectStore` is never bypassed |
| `Settings` (`KAFKA_*` from 2.1) | Configuration. New keys follow the same `KAFKA_*` convention |

## 2. Kafka deployment

- `apache/kafka:4.3.1` as a single node in **KRaft** mode (combined broker + controller), so there's no ZooKeeper.
- It lives under the compose profile `pipeline`, so `make up` is unchanged.
- A fixed `CLUSTER_ID` and a named volume make restarts deterministic. `auto.create.topics.enable=false` means topics exist only if `kafka init` created them.
- Listeners: `INTERNAL kafka:29092` for containers and `EXTERNAL localhost:${KAFKA_PORT:-9092}` for the host. The controller listens on 9093 and isn't published.
- The healthcheck is `kafka-broker-api-versions.sh`. `kafka-init` waits for it, then runs `python -m app.pipeline kafka init` in the pipeline image.
- **No fault tolerance locally.** Replication factor 1 on one broker means one disk. The local setup does not simulate production durability. Production needs RF ≥ 3, `min.insync.replicas=2` and a managed cluster (a deploy-phase decision).

## 3. Topics (created by `kafka init` from `Settings`)

| Topic (setting) | Purpose | Partitions | RF | Retention | Producer | Consumer groups |
|---|---|---|---|---|---|---|
| `quantpilot.news.raw` (`KAFKA_TOPIC_NEWS_RAW`) | News metadata events | 3 | `KAFKA_REPLICATION_FACTOR` (1) | `KAFKA_RAW_RETENTION_HOURS` (168 h) | ingestion publisher | `quantpilot.lake-writer` |
| `quantpilot.market.raw` (`KAFKA_TOPIC_MARKET_RAW`) | Daily bar events | 3 | same | 168 h | ingestion publisher | `quantpilot.lake-writer` |
| `quantpilot.company.raw` (`KAFKA_TOPIC_COMPANY_RAW`) | Company reference events | 1 | same | 168 h | ingestion publisher | `quantpilot.lake-writer` |
| `quantpilot.ingestion.dlq` (`KAFKA_TOPIC_DLQ`) | Dead letters from any component | 1 | same | `KAFKA_DLQ_RETENTION_HOURS` (720 h) | publisher, lake writer | none automatic; `dlq` CLI reads without committing |

- `cleanup.policy=delete`, and `max.message.bytes` is `KAFKA_MAX_MESSAGE_BYTES` plus DLQ headroom.
- `kafka init` is idempotent. It creates missing topics. For existing ones it **verifies** partitions, RF and retention and exits non-zero on drift; it never silently alters a topic.
- Retention is transport retention only. Once an event has landed, the lake is its record, so seven days is the replay window for a stopped lake writer, not an archive.

## 4. Message envelope (schema v1)

- **Value:** the UTF-8 JSON of `IngestionEvent` (`docs/schemas/ingestion_event.v1.json`), produced by pydantic from explicit models. No pickling, and no arbitrary Python objects.
- **Fields:**
  - identity: `schema_version`, `event_id`, `run_id`, `correlation_id`;
  - source: `source`, `source_type`, `domain`, `external_id`, `dedup_key`, `content_hash`;
  - time: `occurred_at`, `ingested_at`;
  - payload: `record` (the typed payload) and `original` (the source's raw JSON).
- **`event_id`** is sha256(`source|external_id|content_hash`). The same content always gives the same ID, and it excludes `ingested_at` / `run_id`.
- **Key:** `event_id` (UTF-8).
- **Headers:** `schema_version`, `domain`, `source`, `event_id`, `run_id` and `correlation_id`. They're for routing and debugging only, and the consumer trusts the value, not the headers.
- **Offsets** come from Kafka. The app only *records* the partition and offset it gets from a delivery report or a consumed message.
- `correlation_id` is additive and optional (default = `run_id`), so v1 messages without it still validate. Any breaking change bumps `schema_version`, and the consumer accepts `SUPPORTED_SCHEMA_VERSIONS`.

## 5. Producer (`app/pipeline/kafka/producer.py`, `app/pipeline/publisher.py`)

- **Settings:** `EventProducer` wraps `confluent_kafka.Producer` with `enable.idempotence=true` (which implies `acks=all`, bounded in-flight requests and retries inside the session), `delivery.timeout.ms=KAFKA_DELIVERY_TIMEOUT_MS` and `message.max.bytes`.
- **Success means acknowledged.** `publish_many` produces, then flushes until every message has a delivery report. A message counts as delivered **only** when its delivery callback reports no error. That report carries the partition and offset Kafka assigned.
- **Bounded retries.** Messages whose report is retriable (`_MSG_TIMED_OUT`, `_TRANSPORT`, `_ALL_BROKERS_DOWN`, `err.retriable()`) are re-produced after `RetryPolicy` backoff, up to `PIPELINE_RETRY_MAX_ATTEMPTS`. A re-produce after a timeout can duplicate a message in Kafka. That's the at-least-once cost, and the consumer absorbs it. Non-retriable errors, such as a message too large or an authorisation failure, fail at once.
- **Idempotence scope.** The idempotent producer removes duplicates caused by its **own internal retries within one producer session**. It does not deduplicate across restarts, across our application-level re-produce, or across runs. Those are handled by `claim_event` (upstream) and the write-once lake (downstream).
- **Tracking integration (`EventPublisher`)**, in order:
  1. `claim_event` for each event, then commit. The result is **NEW** (publish), **RETRY** (retryable status: publish again) or **DUPLICATE** (already delivered: skip).
  2. Publish.
  3. `record_deliveries` sets `published` with partition/offset **only for acknowledged messages**. Failed ones get `publish_failed` with the reason.
  4. Commit.

  A crash between steps 1 and 3 leaves `pending`, so the next run retries. An event encoded larger than the limit is a permanent failure: it goes to the DLQ (component `ingestor`) and is marked `dlq`.
- **Partitioning.** The key is `event_id`, so the same event always hashes to the same partition, and redeliveries and re-produces stay ordered with respect to each other. There is **no global order** and no per-symbol order. That's acceptable because canonical writes are newest-wins by `(source_ingested_at, event_id)` (Step 2.2), which doesn't depend on arrival order. This replaces the `dedup_key` key in `PHASE2_PLAN.md` §4.

## 6. Consumer + lake writer (`app/pipeline/kafka/consumer.py`, `app/pipeline/lake_writer.py`)

- **Group.** A dedicated consumer group, `KAFKA_CONSUMER_GROUP_LAKE` (`quantpilot.lake-writer`), subscribes to the three raw topics. The group owns the lake-writer's offsets only. A future `validation-worker` or processing consumer joins with **its own** group and reads the same topics independently, and nothing in the lake writer changes.
- **Consumer config:** `enable.auto.commit=false`, `enable.auto.offset.store=false`, `auto.offset.reset=earliest`. Messages are handled one at a time per consumer process.
- **Per message:**
  1. **Decode:** size, then UTF-8 JSON, then `schema_version`, then pydantic. Any failure → permanent (`deserialize`, `unsupported_schema_version`, `envelope_invalid` or `payload_invalid`).
  2. **Validate:** `check_event` checks hash, identities, source registry and clock skew. Failure → permanent.
  3. **Routing checks:** the message key must equal `event_id`, and the topic must be the configured topic for `event.domain`. Failure → `envelope_invalid`.
  4. **Already-complete check:** load the `ingestion_records` row. If it already has a `raw_uri` that differs from this event's key, that's **`integrity_conflict`** (the same event would land twice under different keys), and nothing is written.
  5. **Write:** `put_if_absent(raw_key_for(event), encode_event(event))`. The stored bytes are the canonical re-encoding of the validated event, so formatting differences between producers can't cause false conflicts. The outcome:
     - **created**: first landing;
     - **exists with the same SHA-256**: an idempotent replay;
     - **exists with a different SHA-256**: **`integrity_conflict`**. The stored object is never overwritten.
  6. **Record:** `record_landing` sets `landed` with `raw_uri` and the Kafka topic/partition/offset (forward-only, from pending, publish_failed, published or dlq), then commits the DB. If there's no row (an event produced outside the tracked publisher), the object still lands and the message counts as `untracked` in logs and metrics.
  7. **Commit the offset synchronously** (`commit(message, asynchronous=False)`), **after** steps 5 and 6 are durable.
- **Error classes.**
  - Permanent: decode, validation, routing, `InvalidKey`, `integrity_conflict`, and storage refusals such as 403 or 400 (`storage_rejected`).
  - Transient: connection or timeout errors, DB `OperationalError`, `TransientError`. The whole message handler is retried with `RetryPolicy` backoff. The S3 store already retries its own calls; when it gives up (`RetryExhausted`), the lake writer does not retry again.
  - **Unexpected exceptions are not dead-lettered.** They propagate, the process exits without committing, and the supervisor (compose `restart: on-failure`) restarts it, so the message is redelivered. A code bug therefore shows up as a crash loop, not as a DLQ full of mislabelled messages.

## 7. DLQ

- **When:** a message reaches the DLQ only when the failure is **permanent** or the transient **retry budget is exhausted** (`retries_exhausted`, carrying `retry_count`).
- **Contents:** the original value (UTF-8, or base64 if not text; truncated to the size budget with `original_truncated`), original topic/partition/offset/key, `failure_reason`, `error_type`/`error_message`/`error_details`, component, `retry_count`, `first_failed_at`/`failed_at`, and `event_id`/`source`/`run_id` when known.
- **Keys and IDs:** the DLQ key is `dlq_id`, which is deterministic: uuid5 of `component|topic|partition|offset|reason`. **One logical dead letter per failed delivery attempt:** if the lake writer crashes after publishing a dead letter but before committing, the redelivery publishes a copy with the **same** `dlq_id`, so operators and tools can collapse it.
- **Order:** DLQ publish (waits for the ack, with bounded retries), then mark the record `dlq` (forward-only, so a `landed` event hit by a conflicting replay stays `landed`), then commit the offset.
- **If the DLQ publish fails, the original offset is not committed.** The process raises and exits, and the message is redelivered after restart. A message is never dropped because the DLQ was unavailable.
- **Observability:** `python -m app.pipeline dlq` (`make dlq`) lists dead letters without joining a consumer group or committing (payloads hidden unless `--show-payload`). There's also `lake_writer_dlq_total{reason}` and a `pipeline.dlq.published` log line.
- **Replaying from the DLQ** is manual in 2.4, and is documented as a limitation.

## 8. Idempotency and delivery guarantees

| Failure | What happens | Why it's safe |
|---|---|---|
| Producer retry after an ambiguous timeout | Possible duplicate message in Kafka | Same key and bytes, so the lake writer sees an identical object and treats it as a replay |
| Publisher crash after publish, before `record_deliveries` | Record stays `pending`, so the next run's `claim_event` returns RETRY and re-publishes | Same as above; if the envelope differs (new `run_id`), the lake writer reports `integrity_conflict` to the DLQ, and the first landed copy stays authoritative |
| Lake writer crash after the lake write, before the DB commit | Redelivery; the object exists and is identical, so it's a replay; the record then moves to `landed` | Write-once plus hash check |
| Lake writer crash after the DB commit, before the offset commit | Redelivery, then a replay, and `record_landing` doesn't move anything (already `landed`) | Forward-only transitions |
| Kafka broker down during consume | Poll errors are logged, and the consumer resumes from the last committed offset | Offsets are committed only after durable work |

**Guarantee:** at-least-once transport, idempotent consumers, write-once lake and idempotent canonical upserts, so the canonical data is effectively-once. **This is not end-to-end exactly-once.** Kafka transactions would only cover Kafka→Kafka, and the sinks here are S3 and Postgres.

## 9. Observability

- **Registry:** `app/pipeline/metrics.py` is a tiny in-process registry of counters, gauges and summaries with Prometheus-style names and labels. `render_prometheus()` returns text exposition format, ready for a future `/metrics` endpoint or push gateway, and `snapshot()` is logged as `pipeline.metrics`.
  - Producer: `producer_messages_attempted_total`, `producer_messages_acknowledged_total`, `producer_failures_total{reason}`, `producer_retries_total`, `producer_publish_latency_seconds`.
  - Lake writer: `lake_writer_messages_consumed_total{topic}`, `lake_writer_processing_seconds`, `lake_writer_lake_writes_total{result=created|replay}`, `lake_writer_untracked_total`, `lake_writer_failures_total{reason}`, `lake_writer_retries_total`, `lake_writer_dlq_total{reason}`, `lake_writer_commit_failures_total`, `lake_writer_consumer_lag{topic,partition}` (committed position vs high watermark, refreshed periodically).
- **Logs:** structured JSON via `log_event`. They contain IDs, topics, offsets, reasons, hashes and latencies, **never payloads or credentials**.
- There's no Grafana dashboard in this step.

## 10. Configuration (all in `Settings`, validated at startup)

- **Existing keys:** `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC_{NEWS,MARKET,COMPANY}_RAW`, `KAFKA_TOPIC_DLQ`, `KAFKA_CONSUMER_GROUP_LAKE`, `KAFKA_MAX_MESSAGE_BYTES`.
- **New keys:**
  - `KAFKA_CLIENT_ID`;
  - `KAFKA_SECURITY_PROTOCOL` (`PLAINTEXT|SSL|SASL_PLAINTEXT|SASL_SSL`), with `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME` and `KAFKA_SASL_PASSWORD` (a `SecretStr`, required for `SASL_*`);
  - `KAFKA_REPLICATION_FACTOR`, `KAFKA_RAW_RETENTION_HOURS`, `KAFKA_DLQ_RETENTION_HOURS`;
  - `KAFKA_DELIVERY_TIMEOUT_MS`, `KAFKA_POLL_TIMEOUT_SECONDS`.
- **Validation:** bootstrap entries must be `host:port`, SASL credentials are required with SASL protocols, and topic names must be distinct.
- **No hard-coded hosts.** Application code never hard-codes `localhost`: the host default lives in `Settings`, and compose overrides it to `kafka:29092`.

## 11. Commands

- `make kafka-up`: Kafka plus `kafka-init`, then `kafka-check`.
- `make pipeline-up`: MinIO, Kafka, Postgres, `migrate` and the `lake-writer` service.
- `make publish-synthetic`: deterministic SYNTHETIC market events through the tracked publisher, run inside compose against the compose Postgres, **never the dev SQLite**. These use source `synthetic.market` (flagged `synthetic=True` in the source registry), which later steps must exclude from canonical tables.
- `make kafka-check`: topics and consumer-group lag.
- `make dlq`: read the DLQ.
- `make test-kafka`: the kafka test tier.

The CLIs that write tracking state refuse to start unless the database is at Alembic head. They never migrate it themselves.

## 12. Testing architecture

| Tier | Needs | Isolation | Covers |
|---|---|---|---|
| unit | nothing | fakes for the confluent producer/consumer; `LocalObjectStore` in tmp; migrated tmp SQLite | producer ack/retry/exhaustion/permanent errors; lake-writer decode/validate/route/replay/conflict/untracked/DLQ/retry/commit ordering; deterministic `dlq_id`; settings validation; metrics rendering |
| `kafka` | `TEST_KAFKA_BOOTSTRAP` (compose Kafka) | per-test topics `test.<uuid>.*`, group `test.<uuid>.lake-writer`, tmp lake, tmp SQLite; topics and groups deleted afterwards | the 18 scenarios in the Step 2.4 brief, including **crash after the lake write, before the offset commit → redelivery → identical object detected → record still correct → offset committed** |
| existing | as before | as before | SQLite, Postgres and s3 tiers unchanged |

The root `tests/conftest.py` pins Kafka topics to `test.unconfigured.*` and the bootstrap to an unroutable `127.0.0.1:9`, so no test can reach the developer broker by accident.

## 13. Known limitations

- There's one broker with RF 1, so no fault tolerance locally.
- There's no schema registry. Schemas are JSON Schema files, checked for drift in CI.
- The lake writer handles messages sequentially per process. Scaling out means more processes in the same group, up to the partition count.
- DLQ replay is manual.
- A retry that re-publishes an event with a different envelope (new `run_id`) is reported as `integrity_conflict` rather than merged. That's safe, because the first copy wins and the conflict is visible, but it can be noisy after publisher crashes.
- Consumer lag is only visible through logs and metrics snapshots and `kafka check`, with no alerting.
