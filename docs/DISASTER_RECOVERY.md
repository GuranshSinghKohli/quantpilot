# Disaster recovery

No RPO or RTO is claimed. Staging measurements are in [PHASE6_RESULTS.md](./PHASE6_RESULTS.md). They are not a production recovery guarantee.

## PostgreSQL

RDS is configured with `backup_retention_period` from `db_backup_retention_days` (7 in the example), storage encryption, deletion protection, and a final snapshot name. On the Phase 6 staging instance that retention was 7 days, `PubliclyAccessible` was false, and `MultiAZ` was false. A manual snapshot `quantpilot-staging-phase6` was started against that instance. Whether a restore from it completed is recorded in Phase 6 results, not here as a guarantee.

Schema changes are Alembic revisions. The head is `0012`. Production startup does not run migrations. After a restore, run `make prod-migrate` or the migrate task before starting the API and worker. `GET /ready` reports `db_revision` and `migrations_current`.

The developer SQLite file is not a production backup.

## S3

The lake bucket blocks public access, encrypts with AES256, and has versioning enabled. Noncurrent versions expire after 30 days in the lifecycle rule. Versioning was not used as a point-in-time recovery drill. On the Phase 6 staging bucket, `probe_write_once` created `_health/write-once-probe.json`, refused a second write, and read the original bytes back. That is the existing object-store check, not a bucket restore.

## Kafka

One broker, replication factor 1, log directory on the instance root volume. A disk or instance loss drops the log. Retention defaults remain 168 hours for the research topics and 720 hours for the ingestion DLQ. After retention, a message cannot be replayed. Consumers are at-least-once. On staging, `docker stop kafka` left a new research job at `publish_pending` with `attempts` 0. `docker start kafka` let the worker publish it and the job reached `partial`. That is one controlled stop, not high availability. The first broker instance also failed to format its data directory until the bind mount was owned by uid 1000; the replacement instance is the one that served traffic.

## Neo4j

Neo4j is not in this Terraform stack. The graph projection is derived from PostgreSQL. Rebuilding it is `make graph-project` against whatever Neo4j endpoint is configured. That rebuild was not run as a recovery drill.

## Application tasks

API and worker tasks are replaceable. Research leases live in PostgreSQL. A stopped worker leaves a lease until it expires (`RESEARCH_LEASE_SECONDS`, default 120) and another worker can reclaim the job. On staging, stopping the worker task while a job was in flight did not require a reclaim: the job reached `succeeded` during ECS's stop window (about 66 seconds from the stop request to `stoppedAt`), with `attempts` 1. A replacement task then started. An API task stop produced HTTP 503 samples for roughly 48 seconds of an 8-second poll before the replacement returned HTTP 200. That is not a zero-downtime measurement.
