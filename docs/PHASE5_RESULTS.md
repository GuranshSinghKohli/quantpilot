# Phase 5 results

Production deployment configuration is implemented. It was not applied to AWS. No smoke test, failure drill, or restore was run against AWS, so those sections have no measured deployment values.

## A. Objective

Make the existing QuantPilot processes deployable from configuration: environment-gated startup, Terraform for a single-VPC AWS layout, a manual CI deploy workflow, and operator docs. No research feature was added.

## B. Final architecture

Documented in `docs/PHASE5_ARCHITECTURE.md`. ALB, ECS API, ECS Next.js, ECS research worker, RDS PostgreSQL 16, one Kafka EC2 node, private S3. Neo4j is an external URI. This is not a highly available layout.

## C. AWS resources created

None. `terraform apply` was not run.

## D. Services deployed

None. `start_services` defaults to false so a future first apply would not start tasks before secrets exist.

## E. Terraform

`terraform validate` succeeded on this machine with provider `hashicorp/aws` 5.100.0. The provider archive was installed from a local directory after the registry download over IPv6 failed. `terraform fmt -check` passed. The lock file was not committed: a lock generated here only contained `darwin_amd64` checksums and would fail the Linux CI runner. CI resolves `~> 5.70` itself.

## F. Environment and secrets

`ENVIRONMENT=production` refuses SQLite, FakeLLM, localhost Kafka, a MinIO endpoint, and startup migrations. Secret values are not in Git. Terraform creates empty Secrets Manager names. `.env.production.example` has names only.

## G. CI/CD

`ci.yml` gained a Terraform validate job and `scripts/scan-secrets.sh`. `deploy.yml` is `workflow_dispatch` only. Apply uses the GitHub environment `production` and OIDC variables `AWS_ROLE_ARN` and `AWS_REGION`. That workflow was not executed.

## H. Observability

No new metric names. `/health`, `/ready`, and `/metrics` stay as they are. `/ready` still reports Kafka and the lake without making them part of the ready/not-ready flag. The worker calls the existing OpenTelemetry setup when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; the example leaves it empty. ECS logs would go to CloudWatch log groups. No Prometheus or Grafana service was added. No metric series were scraped in this phase.

## I. Security controls

In the Terraform configuration, not on a live account: private subnets for Postgres, Kafka, and the worker; no SSH ingress; RDS not public; S3 public access blocked; task roles limited to the lake bucket and the named secrets; no `AdministratorAccess` on the task roles. API and frontend images drop to non-root users. `scan-secrets.sh` reported `no secret markers` after binary and virtualenv directories were excluded.

## J. Smoke test

Not run. There is no deployed URL.

## K. Failure and recovery tests

Not run on AWS. Existing unit tests still cover publish retry, leases, and idempotency. They are not a substitute for an ECS restart drill.

## L. Backup and recovery validation

Not run. RDS backup retention is set to 7 days in the example variables. No snapshot was restored. No RPO or RTO.

## M. Test results

| Check | Result |
| --- | --- |
| `pytest tests/unit -q` | 643 passed |
| `alembic heads` on a scratch SQLite URL | `0012 (head)` |
| `npx tsc --noEmit` | exit 0 |
| `npm run lint` | no ESLint warnings or errors |
| `terraform validate` | success |
| `scripts/scan-secrets.sh` | `no secret markers` |
| Spark suite | not run; Spark code was not changed |
| Neo4j suite | not run; Neo4j code was not changed |
| Phase 4.8 load matrix | not run |

## N. Data integrity

Developer SQLite `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5`, 876544 bytes. Lake file count 0. Developer Neo4j port 7687 closed. No `.tfstate`, database dump, or credential file is part of the change.

## O. Limitations

- This is not a running production or staging environment.
- HTTPS is off until an ACM certificate ARN is supplied.
- Kafka is one broker, replication factor 1, plaintext on the private network.
- RDS is single-AZ.
- One NAT gateway.
- Worker count is configuration. Phase 4.8's 6.628–7.509 jobs/s figure is a FakeLLM laptop measurement, not this stack.
- Frontend `NEXT_PUBLIC_API_URL` is fixed at image build.
- ECR scan-on-push is configured and was not executed, because no image was pushed.
- The GitHub OIDC provider is created only when `github_repository` is set. An account that already has that provider will need that resource removed before apply.

## P. Deferred

No Phase 6. No new agents, browsing, recommendations, or MCP servers. No Spark cluster, Airflow, or Grafana. No multi-broker Kafka. No measured restore.
