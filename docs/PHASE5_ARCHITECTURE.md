# Phase 5 architecture

This is the deployment configuration in `infra/terraform/`. It has not been applied to an AWS account. Nothing below is a claim that a live environment exists, that it is highly available, or that it has a measured capacity.

Phase 4.8 remains the only load measurement. One worker completed about 6.6–7.5 FakeLLM jobs/s in that development run. That is not a production capacity number.

## Topology

```text
Internet
  → ALB (public subnets, port 80, or 443 when acm_certificate_arn is set)
      → ECS frontend (Next.js, private subnet, port 3000)
      → ECS API (FastAPI, private subnet, port 8000)
          → RDS PostgreSQL 16 (private, system of record, pgvector enabled by migration)
          → Kafka (one EC2, private, port 9092)
          → S3 lake (private bucket, gateway endpoint)
          → OpenAI (egress through one NAT gateway)
ECS research worker (private, no ingress)
  → same Postgres, Kafka, S3, and OpenAI
Neo4j
  → not created here; NEO4J_URI is an external endpoint
```

## What each piece is

| Piece | Role | State | Initial count |
| --- | --- | --- | --- |
| Next.js on ECS | Browser UI | Stateless | 1 task |
| FastAPI on ECS | HTTP API | Stateless | 1 task |
| Research worker on ECS | `python -m app.research worker` | Stateless process, leases in Postgres | 1 task |
| RDS PostgreSQL 16 | System of record | Persistent, single-AZ, encrypted, 7-day backup retention | 1 instance |
| Kafka on EC2 | Event backbone, `apache/kafka:4.3.1`, KRaft | One EBS volume, replication factor 1 | 1 instance |
| S3 bucket | Lake | Durable objects, versioning on, not public | 1 bucket |
| Neo4j | Derived graph | External | Not managed |
| NAT gateway | Private-subnet egress | Single | 1 |

Horizontally adding API tasks is an ECS desired count. It was not tested. Adding worker tasks is `research_worker_count`. Phase 4.8 measured 1, 2, and 4 in-process workers on a laptop, not these ECS tasks, and the gain was not linear.

## Network

- Public subnets: load balancer and the NAT gateway.
- Private subnets: API, frontend, worker, RDS, Kafka.
- Postgres, Kafka, and the worker have no public ingress.
- Kafka accepts 9092 only from the API and worker security groups.
- Postgres accepts 5432 only from the API and worker security groups.
- S3 is reached through a gateway endpoint, not a public bucket policy.
- The load balancer is the only intended public entry. HTTPS is active only when `acm_certificate_arn` is set. With the example variable file it is unset, so the listener is HTTP.

## Application contracts that stay

- PostgreSQL is authoritative. Neo4j and pgvector are derived.
- Kafka delivery stays at-least-once. This phase does not claim exactly-once.
- The worker loop, lease, heartbeat, retry, outbox, and DLQ code are unchanged.
- Research partition default stays 3. Retention defaults stay 168 hours for research topics and 720 hours for the ingestion DLQ.
- Topic names stay `quantpilot.research.request`, `quantpilot.research.completed`, and `quantpilot.research.dlq`, plus the existing ingestion topics.
- Message key stays `research_id`. Order is per partition only.
- `ENVIRONMENT=production` does not migrate on startup.
