# Disaster recovery

No restore was executed. No RPO or RTO is claimed.

## PostgreSQL

RDS is configured with `backup_retention_period` from `db_backup_retention_days` (7 in the example), storage encryption, deletion protection, and a final snapshot name. Automated backups are an RDS feature of that setting. Restoring a snapshot, and the time that takes, were not measured.

Schema changes are Alembic revisions. The head is `0012`. Production startup does not run migrations. After a restore, run `make prod-migrate` or the migrate task before starting the API and worker. `GET /ready` reports `db_revision` and `migrations_current`.

The developer SQLite file is not a production backup.

## S3

The lake bucket blocks public access, encrypts with AES256, and has versioning enabled. Noncurrent versions expire after 30 days in the lifecycle rule. Versioning was not used in a tested recovery. The application still uses the existing object store: `put_if_absent` does not replace an object, and SHA-256 is stored as object metadata. That behavior was not re-tested against AWS S3 in this phase.

## Kafka

One broker, replication factor 1, log directory on the instance root volume. A disk or instance loss drops the log. Retention defaults remain 168 hours for the research topics and 720 hours for the ingestion DLQ. After retention, a message cannot be replayed. Consumers are at-least-once. Replaying a retained research request is still idempotent at the Postgres sink: a terminal job does not insert a second claim set. That idempotency was established before this phase and was not re-run against this Kafka instance, because this instance was not created.

## Neo4j

Neo4j is not in this Terraform stack. The graph projection is derived from PostgreSQL. Rebuilding it is `make graph-project` against whatever Neo4j endpoint is configured. That rebuild was not run as a recovery drill.

## Application tasks

API and worker tasks are replaceable. Research leases live in PostgreSQL. A stopped worker leaves a lease until it expires (`RESEARCH_LEASE_SECONDS`, default 120) and another worker can reclaim the job. That behavior was not re-tested on ECS.
