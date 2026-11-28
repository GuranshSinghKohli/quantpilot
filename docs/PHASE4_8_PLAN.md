# Phase 4.8 — Load testing and system capacity validation

Phase 4.7 is unchanged at `4cb28d02458e5070ab5c58cd49ed4b7489cde2bf`. Alembic head stays `0012`. No migration.

These results are a DEVELOPMENT LOAD TEST, FAKELLM, and FIXTURE-BASED. They are not production model quality and not a production capacity guarantee.

These results characterize the tested development environment and are not production capacity guarantees.

## Method

The harness is `python -m app.research load`. It calls the existing `create_research` handler, the transactional outbox, and `research.worker.serve`. It does not start a second research runner and it does not start the compose `research-worker` (that process uses the compose Postgres, not the disposable database).

Workers are in-process threads. They have no cgroup CPU or memory limit. The handler latency is in-process. It does not include a network round trip.

The disposable database is `quantpilot_load` on qp-pg-test (`postgresql://quantpilot:quantpilot@localhost:55432/quantpilot_load`). The harness refuses SQLite, port 5432, and any other database name. Kafka topics and the consumer group are created under a `test.<hex>` prefix and deleted afterward. The database is dropped at the end. Research does not query Neo4j for this run.

The matrix uses `RESEARCH_MAX_QUEUED_JOBS=10000` on the harness settings object so a 100-request level is not rejected by the cap. The production code default remains 100. The queue-limit scenario sets a cap from the durable count of `publish_pending + queued + running`.

SQLAlchemy pool for the harness: `pool_size=20`, `max_overflow=40`, `pool_timeout=30s`. Kafka settings are the production defaults: 3 research partitions, replication factor 1, poll timeout 1.0s, delivery timeout 30000ms. The message key remains `research_id`. Order is per partition only.

FakeLLM is the only model. Token totals below come from `agent_runs` rows written by FakeLLM's character estimate. Cost is null. No pricing configuration was set, and no figure below is a billed cost.

## Percentiles and throughput

One method is used for p50, p95, and p99: nearest-rank, `rank = ceil(percentile/100 * n)`, 1-based, clamped to `[1, n]`.

With n=100, p99 is the 99th ordered sample. The 40-request and 10-request scenarios are below that size. Their p99 values are reported in the JSON with an explicit small-sample note and are not used as the capacity table.

Stage clocks:

| Stage | Definition |
| --- | --- |
| Submission | `create_research` plus commit, in-process |
| Outbox publish | `research_outbox.published_at - created_at` |
| Post-publish wait | `research_jobs.started_at - published_at` |
| Execution | `finished_at - started_at`, including the graph and persistence |
| End-to-end | `finished_at - create_research start` |

`research_jobs.queued_at` is the insert time, so the worker metric `research_job_queue_wait_seconds` includes outbox delay. The queue column below is the post-publish wait, not that metric.

Completion throughput is succeeded plus partial jobs divided by the wall clock from the first submit start to the last `finished_at`. Submission throughput is successful submits divided by the submit wall clock. Submission throughput is not system throughput.

## Environment

| Item | Measured value |
| --- | --- |
| OS | macOS 15.8, x86_64 |
| CPU | 12 cores, Intel Core i7-9750H @ 2.60GHz |
| RAM | 34359738368 bytes (32 GiB) |
| Python | 3.11.15 |
| Docker | 29.7.2 |
| PostgreSQL | 16.15, `max_connections=100`, `shared_buffers=128MB`, container `qp-pg-test` |
| Kafka | `apache/kafka:4.3.1`, container `quantpilot-kafka-1`, bootstrap `localhost:9092` |
| Workers | 1 in the matrix; 1, 2, and 4 in the scaling run |
| LLM | FakeLLMProvider |
| Embeddings | FakeEmbeddingProvider |

Resource samples: Docker stats and `pg_stat_activity` every 2.0 seconds. Kafka committed lag every 0.5 seconds, as high watermark minus the consumer group's committed offset. A partition with no commit is treated as offset 0. Docker's CPU percentage can exceed 100 because it is summed across cores.

## Workload

100 requests per concurrency level, seed-independent largest-remainder mix of existing evaluation queries. Sources are the `example.com` fixtures seeded by the evaluation helper. No external market data was added.

| Kind | Share of 100 |
| --- | --- |
| news | 20 |
| company | 20 |
| technology | 20 |
| market | 20 |
| multi-agent | 16 |
| no-result (`European airport fog delays`) | 2 |
| partial-agent (`AMD Instinct Meta data centers`) | 2 |

Queries inside a kind rotate through the fixture list, so the 20 news requests are not one string repeated. Conflict and forced agent failure are separate scenarios because they need a different FakeLLM configuration.

Concurrency 100 ran after concurrency 50 finished with 0 failures, 0 incomplete jobs, and a completion p95 that did not triple the concurrency-25 p95. That gate is a safety check for this laptop. It is not a certified maximum.

## One worker

All 600 jobs succeeded. Failed = 0. Duplicate jobs, duplicate claim statements, and duplicate reports = 0. DLQ messages = 0. End lag = 0 after every level. No completion p95 doubled from one level to the next (`latency_jumps` is empty).

| Workers | Concurrency | Requests | Completed | Failed | p50 (s) | p95 (s) | p99 (s) | Completion jobs/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 100 | 100 | 0 | 6.884 | 11.679 | 12.026 | 6.906 |
| 1 | 5 | 100 | 100 | 0 | 7.408 | 12.364 | 12.713 | 7.258 |
| 1 | 10 | 100 | 100 | 0 | 8.248 | 13.615 | 14.116 | 6.628 |
| 1 | 25 | 100 | 100 | 0 | 6.778 | 12.106 | 12.563 | 7.509 |
| 1 | 50 | 100 | 100 | 0 | 8.038 | 13.069 | 13.508 | 7.002 |
| 1 | 100 | 100 | 100 | 0 | 8.172 | 13.531 | 13.898 | 6.772 |

p50/p95/p99 in that table are end-to-end seconds.

| Concurrency | Submission p50/p95/p99 (s) | Queue p50/p95/p99 (s) | Publish p50/p95/p99 (s) | Execution p50/p95/p99 (s) |
| --- | --- | --- | --- | --- |
| 1 | 0.023 / 0.037 / 0.044 | 6.334 / 11.525 / 11.748 | 0.422 / 0.729 / 0.804 | 0.056 / 0.141 / 0.158 |
| 5 | 0.045 / 0.087 / 0.100 | 6.349 / 10.828 / 11.218 | 0.960 / 1.530 / 1.557 | 0.052 / 0.089 / 0.107 |
| 10 | 0.088 / 0.128 / 0.148 | 6.852 / 11.995 / 12.545 | 1.236 / 1.561 / 1.577 | 0.058 / 0.102 / 0.170 |
| 25 | 0.147 / 0.281 / 0.333 | 5.642 / 10.784 / 11.292 | 0.893 / 1.182 / 1.201 | 0.055 / 0.084 / 0.090 |
| 50 | 0.207 / 0.327 / 0.399 | 6.463 / 10.746 / 11.211 | 1.356 / 2.035 / 2.042 | 0.056 / 0.080 / 0.111 |
| 100 | 0.213 / 0.368 / 0.487 | 6.908 / 11.910 / 12.399 | 0.934 / 1.339 / 1.360 | 0.055 / 0.114 / 0.157 |

Submission throughput at these levels was 36.9, 75.2, 78.8, 96.9, 75.1, and 82.8 requests/s. That is faster than completion and is not the system throughput.

| Concurrency | Queue p95 (s) | Peak Kafka lag | End lag | DB connection errors | Duplicate claims |
| --- | --- | --- | --- | --- | --- |
| 1 | 11.525 | 94 | 0 | 0 | 0 |
| 5 | 10.828 | 95 | 0 | 0 | 0 |
| 10 | 11.995 | 94 | 0 | 0 | 0 |
| 25 | 10.784 | 93 | 0 | 0 | 0 |
| 50 | 10.746 | 94 | 0 | 0 | 0 |
| 100 | 11.910 | 96 | 0 | 0 | 0 |

Deadlocks = 0 and lock waits = 0 at every level. `pg_stat_database.xact_rollback` increased by about 200 per level because read-only sessions roll back on close. That counter is not a failed research transaction. Outbox pending after each level was 0. The in-process `research_outbox_pending` gauge was 0 at the end of the run.

Request watermarks are cumulative on one topic. Per-level partition counts are the difference:

| Concurrency | Partition 0 | Partition 1 | Partition 2 |
| --- | --- | --- | --- |
| 1 | 30 | 44 | 26 |
| 5 | 35 | 33 | 32 |
| 10 | 32 | 34 | 34 |
| 25 | 30 | 32 | 38 |
| 50 | 26 | 39 | 35 |
| 100 | 43 | 30 | 27 |

Each level produced 100 request messages and 100 completion messages. DLQ stayed at 0. Messages landed on all 3 partitions. That is not global order.

`research_queue_published_total` increased by 200 per level: 100 request events and 100 completion events. Worker messages, successes, and failures matched 100 / 100 / 0. Reclaims and publish failures were 0.

Sampled Postgres connections peaked at 3, 6, 11, 20, 20, and 20. The harness pool size is 20. `max_connections` is 100. No connection error was recorded. Peak container CPU during the matrix was 88.51% for `qp-pg-test` (concurrency 50) and 280.22% for Kafka (concurrency 10). Last sampled memory was about 264–320 MiB for Postgres and 1.38–1.49 GiB for Kafka, against 7.75 GiB reported by Docker. The sampling interval was 2.0 seconds, with 5 or 6 samples per level.

FakeLLM token sums on each 100-request level were 84756 in and 38321 out. Cost is null.

## Worker scaling

Same 100-request mix, concurrency 10.

| Workers | Concurrency | Requests | Completed | Failed | p50 (s) | p95 (s) | p99 (s) | Completion jobs/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 10 | 100 | 100 | 0 | 7.942 | 13.152 | 13.675 | 6.763 |
| 2 | 10 | 100 | 100 | 0 | 5.466 | 9.023 | 9.697 | 9.501 |
| 4 | 10 | 100 | 100 | 0 | 3.973 | 6.617 | 6.996 | 12.854 |

Queue p95 was 11.561s, 8.188s, and 6.064s. Peak lag was 96, 95, and 92, and end lag was 0. Duplicate claims were 0. Connection errors and deadlocks were 0. Sampled connections peaked at 20 for each worker count. Execution p50 rose slightly, from 0.055s to 0.084s, as worker count went from 1 to 4.

Two workers completed the 100 jobs in 10.525 seconds. Four workers completed them in 7.780 seconds. Throughput from 1 to 2 workers was 6.763 to 9.501 jobs/s (1.40x). From 1 to 4 workers it was 6.763 to 12.854 jobs/s (1.90x). The topic has 3 partitions, so a fourth consumer does not receive its own partition. This is not linear scaling.

## Correctness scenarios

| Scenario | Result |
| --- | --- |
| Idempotency | 40 logical requests plus 8 duplicate submits (20%). 8/8 returned the existing `research_id`. 0 duplicate jobs, claims, or reports. |
| Same key, 8 concurrent submits | 1 distinct job. 0 errors. |
| Different keys, 8 concurrent submits | 8 distinct jobs. 0 errors. |
| Replay | 10 published request messages were produced again. Status stayed succeeded. Claim rows stayed 38. Duplicate claims and reports stayed 0. Delivery remains at-least-once, not exactly-once. |
| Crash | The first worker claimed the lease and did not finish. `lease_until` was set to the past. Production `RESEARCH_LEASE_SECONDS` stayed 120. The next worker completed the job as succeeded, with 10 claim rows and 0 duplicate claims or reports. |
| Outbox pause | 20 request publishes were rejected while the gate was closed. Pending outbox rows = 20. `publish_pending` jobs = 20. After the gate opened, all 20 succeeded, pending outbox = 0, duplicates = 0. |
| Queue cap | Active jobs before the test = 0. Cap set to 2 from that durable count. Status codes were 202, 202, 429. |
| One publish failure | 1 injected `DeliveryFailed`. The 10 jobs then succeeded. Pending outbox = 0. Duplicates = 0. The failing batch's published count was 11 because earlier contention rows were still pending in the same outbox. |
| Database statement timeout | One `SET LOCAL statement_timeout = '1ms'` around `pg_sleep` raised `OperationalError`. The following insert was rolled back. No job row remained. No container was killed. |
| 5% FakeLLM timeouts | 40 requests. Every 20th `generate_structured` raised a retryable timeout (6 injections). 40 succeeded, 0 partial, 0 failed, 0 worker retries, 0 DLQ. n=40, so p99 is not a stable tail. End-to-end p95 was 6.652s. |
| 10% FakeLLM timeouts | 40 requests. Every 10th call raised (13 injections). 40 succeeded, 0 worker retries, 0 DLQ. End-to-end p95 was 10.021s. |
| News agent forced failure | 10 news-kind requests, `fail_agents={news}`. 5 partial and 5 succeeded. Worker partial counter = 5. 0 retries and 0 DLQ. The two fixture queries do not both select news. |
| Conflicting FakeLLM scenario | 10 multi-agent requests. 10 succeeded. 0 duplicate claims. |

The timeout injection did not reach the worker retry path. The graph catches `LLMError` in the planner (deterministic fallback) and in report assembly (summary bullets omitted), so those calls still finish as succeeded. Worker `research_worker_retries_total` stayed 0. This run does not measure OpenAI retry latency.

## What the measurements support

At every one-worker level, the largest p50 stage was post-publish wait (about 5.6–6.9s). Execution p50 stayed about 0.055s. FakeLLM makes that execution time unrealistically fast relative to a hosted model. Do not read these execution times as OpenAI latency.

With one worker, completion throughput stayed between 6.628 and 7.509 jobs/s from concurrency 1 through 100. Completion p95 stayed between 11.679s and 13.615s. Peak lag rose to 93–96 messages while the single worker drained a published batch, then returned to 0. There was no concurrency level in this run where p95 doubled, failures appeared, or lag remained after the level finished. That is not an observed saturation ceiling and it is not a maximum capacity.

The constraint this run actually shows is serial consumption by one worker after the outbox publishes a batch. Two workers at concurrency 10 raised completion throughput from 6.763 to 9.501 jobs/s and cut p95 from 13.152s to 9.023s. Four workers reached 12.854 jobs/s and p95 6.617s. The gain from 2 to 4 workers was smaller than the gain from 1 to 2, on a topic with 3 partitions.

Postgres did not run out of connections. The sampled peak was 20, equal to the harness pool size, with `max_connections` at 100 and zero connection errors.

## Observability

Metric label keys recorded during the run: `agent`, `component`, `error_category`, `error_type`, `finding_type`, `model`, `operation`, `source`, `status`, `verification_status`. Forbidden keys were empty. No metric label carried a research id, query, claim, URL, ticker, or correlation id.

## Data integrity

Developer SQLite before and after: `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5`, 876544 bytes. Developer lake file count stayed 0. Developer Neo4j port 7687 was closed before and after. The harness only probed that port. It did not open a Neo4j session. `quantpilot_load` was dropped after the run.

## Limitations

- FakeLLM execution is not hosted-model latency, throughput, or cost.
- Workers were threads in the client process, not containers with CPU or memory limits.
- Handler timing excludes HTTP network time.
- One broker, replication factor 1, and 3 partitions.
- The 40-request failure runs and the 10-request partial and conflict runs are too small for a stable p99.
- Injected LLM timeouts were absorbed in the graph and did not exercise worker requeue.
- Docker CPU percentages above 100 are multi-core stats, not machine utilization over 100%.

## Deferred

No Phase 4.9 work. A manual real-LLM benchmark was not run. It would have to be opt-in, kept out of CI, and kept free of committed credentials. This phase does not add one.
