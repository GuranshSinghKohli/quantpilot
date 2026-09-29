# Phase 4.9 results

These results characterize the tested development environment and are not production capacity guarantees. They are a DEVELOPMENT LOAD TEST, FAKELLM, and FIXTURE-BASED measurement. They are not production model quality.

## 1. Objective

Use the Phase 4.8 measurements to change only a bottleneck that those measurements support.

## 2. Phase 4.8 baseline

Authoritative run: commit `bd6446076b98d313cfb315b0116dd7d1c0ff1736`, report left at `/tmp/quantpilot-research-load.json` and not committed.

The earlier full-matrix process failed with `SyntaxError` in `loadtest.py` after about 4 seconds. It failed while importing the harness. Zero research requests were submitted. That process is excluded from every latency, throughput, retry, and failure figure below.

Environment: macOS 15.8, 12 cores, Intel Core i7-9750H, 34359738368 bytes RAM, Python 3.11.15, Docker 29.7.2, PostgreSQL 16.15 on `qp-pg-test` (`max_connections=100`, `shared_buffers=128MB`), Kafka `apache/kafka:4.3.1` at `localhost:9092`, 3 partitions, replication factor 1. Workers were in-process threads. `FakeLLMProvider`. `openai_calls` false. Cost null.

One worker, 100 requests, all succeeded:

| Concurrency | End-to-end p50/p95/p99 (s) | Queue p50 (s) | Execution p50 (s) | Completion jobs/s |
| --- | --- | --- | --- | --- |
| 1 | 6.884 / 11.679 / 12.026 | 6.334 | 0.056 | 6.906 |
| 5 | 7.408 / 12.364 / 12.713 | 6.349 | 0.052 | 7.258 |
| 10 | 8.248 / 13.615 / 14.116 | 6.852 | 0.058 | 6.628 |
| 25 | 6.778 / 12.106 / 12.563 | 5.642 | 0.055 | 7.509 |
| 50 | 8.038 / 13.069 / 13.508 | 6.463 | 0.056 | 7.002 |
| 100 | 8.172 / 13.531 / 13.898 | 6.908 | 0.055 | 6.772 |

## 3. Measured bottleneck

One `serve()` loop. It publishes the outbox, executes at most the due reclaimed jobs, polls one Kafka message, runs that leased job, and commits the offset. After the outbox publishes a batch, the rest of the batch waits on that loop.

## 4. Evidence

- Post-publish wait was the dominant p50 stage at concurrency 1, 5, 10, 25, 50, and 100 (`dominant_stage_by_level`).
- Completion throughput stayed between 6.628 and 7.509 jobs/s while submission concurrency rose from 1 to 100. `latency_jumps` was empty. End lag was 0. This is a flat single-consumer rate, not a saturation ceiling.
- Execution p50 stayed about 0.055s, so the graph was not the multi-second wait.
- Two workers at concurrency 10 moved completion throughput from 6.763 to 9.501 jobs/s and end-to-end p95 from 13.152s to 9.023s. Four workers reached 12.854 jobs/s and p95 6.617s.
- PostgreSQL, Kafka lag, and the outbox were not the hold: 0 connection errors, 0 lock waits, 0 publish failures, end lag 0, pending outbox 0 after each level.

## 5. Change implemented

`publish_pending` now sets `research_outbox_pending` after `flush`, from rows whose status is still `pending`.

Previously the count ran after `claim_pending` and before those rows were marked `published`. The gauge could remain at the in-flight batch size until a later call. Phase 4.8 used the SQL pending count as the backlog figure for that reason. The end-of-run gauge was already 0.0.

No worker, Kafka, lease, graph, or schema change.

The host load smoke test referenced a name that exists only inside its skip helper. On a host where qp-pg-test and localhost Kafka are both up, that test raised `NameError` before submitting the four requests. It now passes `TEST_KAFKA_BOOTSTRAP`. Spark still skips the test, because its broker host is not `localhost`.

## 6. Why this change

The serial drain is already addressed by running more than one worker process. That was measured. Putting several executions on one consumer would change offset commit and lease behavior. That is a worker redesign, and the 4.8 scaling result does not require it.

The gauge was the one production signal that did not match durable outbox state during a batch. The fix is a count of the same rows, after their status updates. It does not add a label.

## 7. Before and after benchmarks

No second matrix was run. The timed path is the same `serve()` loop, the same batch size, and the same single offset commit. Moving an existing count to after `flush` does not change submission, queue, execution, or completion throughput.

Before and after for those figures are the Phase 4.8 table in section 2. Completion throughput was not claimed to improve.

## 8. Reliability regression

`tests/unit/research/test_async.py` covers duplicate submit, idempotency conflict, queue limit, publish failure and retry, a second publisher not taking a locked row, worker success, duplicate delivery, partial specialist failure, and terminal redelivery. The publish test now also expects the pending gauge to be 1 after a failed attempt and 0 after the row is published.

The Phase 4.8 scenario results are unchanged because this patch does not alter those paths:

- 40 logical requests + 8 duplicates: 8/8 idempotency hits, 0 duplicate jobs, claims, or reports.
- Same key ×8: 1 job. Different keys ×8: 8 jobs.
- Replay of 10 messages: claim rows stayed 38.
- Crash/reclaim: final status succeeded, 10 claim rows, 0 duplicates, `RESEARCH_LEASE_SECONDS` stayed 120.
- Outbox pause: 20 pending, then 20 succeeded.
- Queue cap from durable state: 202, 202, 429.
- One injected publish failure: the 10 jobs succeeded.
- Statement timeout: rollback, no job row, no container kill.
- Worker retry counter stayed 0 under injected LLM timeouts because the graph catches `LLMError`.

## 9. Data integrity

Developer SQLite checked after this phase: `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5`, 876544 bytes. Developer lake file count 0. Developer Neo4j port 7687 closed. Alembic head remains `0012`. No migration. No benchmark JSON committed.

## 10. Remaining limitations

- FakeLLM execution is not hosted-model latency or cost.
- One worker still drains a published batch serially. Throughput stays near 7 jobs/s in this environment until more worker processes are run.
- Four workers on 3 partitions reached 1.90× the one-worker rate, not 4×.
- The 40-request failure scenarios are too small for a stable p99.
- Injected LLM timeouts still do not exercise worker requeue.

## 11. Deferred

No Phase 5. No second queue. No in-process consumer pool. No partition-count change. No real-LLM benchmark. No Prometheus or Grafana install.
