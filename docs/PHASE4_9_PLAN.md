# Phase 4.9 — Measured bottleneck fixes and production hardening

Phase 4.8 is complete at `bd6446076b98d313cfb315b0116dd7d1c0ff1736`. This phase uses that run only.

An earlier full-matrix invocation raised `SyntaxError` in `loadtest.py` and exited before `create_research`. It submitted zero requests. It is not a latency, throughput, retry, or failure sample. The later full FakeLLM matrix is the baseline.

These results characterize the tested development environment and are not production capacity guarantees.

## Bottleneck rule

A component is a bottleneck only when Phase 4.8 measured it. Queue wait can qualify. A component that stayed fast, returned to zero lag, or recorded zero errors does not qualify because it might matter later.

## Component table

The numbers below are the one-worker matrix unless a row says otherwise. Percentiles are nearest-rank. n=100.

| Component | Measured evidence | Latency | Throughput / errors | Bottleneck | Action |
| --- | --- | --- | --- | --- | --- |
| API submission | In-process `create_research`. Rejected submissions 0. | Submission p50 0.023–0.213s. p95 0.037–0.368s. | Submission throughput 36.892–96.903 requests/s, above completion throughput. | No | None |
| PostgreSQL | `max_connections=100`. Peak connections 3, 6, 11, then 20 (harness pool size). | Not a separate query profile. | Connection errors 0. Deadlocks 0. Lock waits 0. `xact_rollback` rose because read-only sessions close uncommitted. | No | None. No index. |
| Outbox publish | Publish is not the dominant stage. Pending SQL count was 0 after every level. | Publish p50 0.422–1.356s. p95 0.729–2.035s. | Publish failures 0. `research_queue_published_total` +200 per 100 jobs (request + completion). | No | Gauge only. Count pending rows after status updates. |
| Kafka | 3 partitions, replication factor 1, poll 1.0s. Lag sampled every 0.5s. | Not a broker-side produce profile. | Peak lag 93–96, end lag 0. DLQ 0. Each level: 100 request and 100 completion messages. | No persistent lag | None. Do not change producer batching. |
| Worker / queueing | Dominant stage at every one-worker level was post-publish wait. One `serve()` loop runs one leased job, then commits the offset. | Queue p50 5.642–6.908s. p95 10.746–11.995s. | Completion 6.628–7.509 jobs/s from concurrency 1 through 100. Retries 0. Reclaims 0. | Yes. Serial drain after a published batch. | No code change. More worker processes already measured. |
| LangGraph execution | `finished_at - started_at`, including persistence. | Execution p50 about 0.055s. p95 0.080–0.141s. | 100/100 succeeded on the matrix. | No | None |
| FakeLLM | Default provider. Token sums 84756 in / 38321 out per 100 jobs. Cost null. | Included in the 0.055s execution p50. | 5% and 10% injected timeouts were absorbed in the graph. Worker retries stayed 0. | No. Unrealistically fast versus a hosted model. | None. Do not extrapolate to OpenAI. |
| Evidence / retrieval | Inside execution, on evaluation fixtures. | Not separately timed. Execution p50 0.055s bounds it. | No retrieval error in the matrix. | No | None |
| Neo4j | Research load did not open a Neo4j session. Port 7687 stayed closed. | None | None | No | None |
| pgvector | Fixture path. Not broken out from execution. | Bounded by execution p50 0.055s. | No index miss was measured. | No | None |
| Serialization | No separate measurement. | None | None | No | None |
| Resource use | Docker stats every 2.0s. | n/a | Postgres peak CPU 88.51% at concurrency 50. Kafka peak CPU 280.22% at concurrency 10 (multi-core stat). No connection exhaustion. | No machine saturation | None |

## What the worker row means

At concurrency 1, 100 jobs finished in 14.481s (6.906 jobs/s) while execution p50 was 0.056s. Jobs were published quickly and then waited. Peak lag of 94 messages returning to 0 is that same drain, not a stuck consumer.

Scaling at concurrency 10, 100 requests:

| Workers | Completion jobs/s | End-to-end p95 (s) | Queue p95 (s) | Peak lag | End lag |
| --- | --- | --- | --- | --- | --- |
| 1 | 6.763 | 13.152 | 11.561 | 96 | 0 |
| 2 | 9.501 | 9.023 | 8.188 | 95 | 0 |
| 4 | 12.854 | 6.617 | 6.064 | 92 | 0 |

Two workers were 1.40× the one-worker rate. Four workers were 1.90×. The topic has 3 partitions. Two workers still had a free partition and did not double throughput, so raising the default partition count is not supported as the fix.

## Changes that are in scope

- Set `research_outbox_pending` from rows still `pending` after the batch is marked. The previous count ran while claimed rows were still `pending`, so the gauge could sit at the batch size after those rows had been published.
- Point the four-request host smoke test at `TEST_KAFKA_BOOTSTRAP`. It referenced a local variable from the skip helper and raised `NameError` once the host actually entered the test.
- Document the baseline, the excluded syntax-error invocation, and the components that were not changed.

## Changes that are out of scope

- A second consumer thread inside one Kafka consumer.
- A new queue, worker framework, or research-graph rewrite.
- Lease, retry, DLQ, idempotency, or evaluation-label edits.
- A new Alembic revision.
- A second full matrix. The timed path is unchanged, so Phase 4.8 remains the before and after measurement.
