# Phase 6 results

QuantPilot was deployed to an isolated AWS staging stack and exercised end to end. This is not a production deployment, and it is not a capacity test. Phase 4.8 remains the only formal load matrix.

Every number below is from `quantpilot-staging` in account `225201316318`, region `us-east-1`, unless a local check says otherwise. No passwords, API keys, or secret strings are recorded here.

## A. Staging environment

- Name prefix: `quantpilot-staging`
- Application mode on ECS tasks: `ENVIRONMENT=production` (real Postgres, `LLM_PROVIDER=openai`, `DB_AUTO_MIGRATE=false`)
- Terraform variable `environment=staging` is only the resource prefix
- VPC `vpc-091446e8f63edbee9`, CIDR `10.42.0.0/16` (chosen so it does not overlap `10.40.0.0/16` already present in this account)
- Plans reviewed before apply did not include resources named `merchantos`
- Local Terraform state stays gitignored. `environments/staging.tfvars` stays gitignored. Non-secret values are in `environments/staging.tfvars.example`
- GitHub `deploy.yml` was not run. `github_repository` was empty, so no OIDC deploy role was created

## B. AWS region

`us-east-1`. Terraform v1.16.0 on darwin_amd64. Provider hashicorp/aws v5.100.0.

## C. Infrastructure deployed

| Piece | Identifier |
| --- | --- |
| VPC | `vpc-091446e8f63edbee9` (`10.42.0.0/16`) |
| NAT | `nat-0c03ad588a9d428c0` |
| ALB DNS | `quantpilot-staging-1583627039.us-east-1.elb.amazonaws.com` |
| ALB public address observed | `107.20.146.101` |
| ECS cluster | `quantpilot-staging` |
| ECS services | `api`, `frontend`, `research-worker` (desired 1 after the start apply) |
| RDS | `quantpilot-staging.cc1w0uiuww3e.us-east-1.rds.amazonaws.com`, id `db-UDQBCGBOSASGEFIZYSQAJVAXJM`, `db.t4g.micro`, PostgreSQL, AZ `us-east-1a` |
| RDS DNS | `10.42.10.21` (private) |
| Kafka | `i-0b4dc7cc8e237f7fa`, private `10.42.10.160:9092`, no public IP. Image `apache/kafka:4.3.1` digest `sha256:77e3df9054047a88b520d0cc46e16696d3b22022e1d580aeccd2632df6532837` |
| S3 | `quantpilot-staging-lake-225201316318` |
| Secrets (names only) | `quantpilot-staging/database-url`, `quantpilot-staging/openai-api-key`, `quantpilot-staging/jwt-secret`, plus the RDS-managed master secret |

RDS `PubliclyAccessible=false`, `MultiAZ=false`, `BackupRetentionPeriod=7`.

Security groups: Postgres `5432` and Kafka `9092` allow only the API security group `sg-03825bc1c1be57e1f` and the worker security group `sg-0b8f7f4626235d51a`. The ALB security group allows TCP 80 and 443 from `0.0.0.0/0`. Only an HTTP listener exists.

## D. Terraform plan/apply result

The first apply created the stack and then failed: ELBv2 allows at most five path values on one listener rule. The API paths were split into two rules (`/api/*`, `/health`, `/ready`, `/metrics` and `/docs`, `/redoc`, `/openapi.json`). The follow-up plan was 3 to add, 0 to change, 0 to destroy, and that apply created the two rules and the API service.

Later applies in this phase, each reviewed as staging-only:

| Apply | Result | Duration |
| --- | --- | --- |
| Kafka user-data replacement (data-dir ownership and image-pull retry) | 3 added, 2 changed, 3 destroyed. Destroyed the failed broker `i-00121ce30b6351144` and replaced the API and worker task definitions so `KAFKA_BOOTSTRAP_SERVERS` followed the new private IP | 51.5s |
| Start services and point the frontend task at `:phase6` | 1 added, 3 changed, 1 destroyed. Desired count 0 → 1 for api, frontend, and research-worker | 10.9s |
| Point API, worker, and migrate tasks at `:phase6-worker` | 3 added, 2 changed, 3 destroyed | 7.7s |

The Kafka replacement was required. The first broker's cloud-init log showed `docker pull` timing out through the NAT gateway, and after a manual pull the container crash-looped with `AccessDeniedException` on `/var/lib/kafka/data` because the image runs as uid 1000. User-data now `chown 1000:1000` and retries the pull. The replacement instance logged `Kafka Server started` and listened on `0.0.0.0:9092`.

A second blocker: `python -m app.research worker` called `serve()` with the test default `idle_timeout_s=30`, so the first ECS worker task exited 0 after the topic was idle. The CLI now passes `idle_timeout_s=0`. Tests that call `serve()` still pass their own timeout. After image `phase6-worker`, the worker task stayed up and consumed jobs.

`ALLOW_HTTP_ORIGINS=true` is set only when `acm_certificate_arn` is empty. Localhost origins are still refused. This exists because the staging listener is HTTP.

## E. Image build/push result

Images were built from the existing Dockerfiles. No second container layout was added.

| Image | Tag | Digest |
| --- | --- | --- |
| API and worker (same build, `REQUIREMENTS=requirements-pipeline.txt`) | `phase6` | `sha256:5dc05aaee4b36660ab68d841545bf0952b5d69bbe67249bd95fc5d5b09dc1600` |
| API and worker after the idle-timeout fix | `phase6-worker` | `sha256:76102d68bb107718020406bbde6abe68572f3d77944048615681a12190302876` |
| Frontend (`NEXT_PUBLIC_API_URL` baked to the ALB HTTP host) | `phase6` | `sha256:562cb66d35f61c3cd5c60a6872ff7da5d00813983c2f5ae82832d5dbb40d40dd` |

Running tasks use `phase6-worker` for the API and worker and `phase6` for the frontend. ECR tag mutability is `IMMUTABLE`. `latest` was not used.

Image config: API user `quantpilot`, frontend user `node`. The API image config JSON did not contain `sk-`, `AKIA`, a private key, or a `postgres://` URL.

## F. Database migration result

`make prod-migrate` cannot reach this RDS instance from a laptop: the hostname resolves to `10.42.10.21`. The migrate task runs the same command, `alembic upgrade head`, on Fargate in the private subnets.

- Task `ac096d9b44704400b3b8bd22cf196d1a` exited 0. The log shows upgrades `0001` through `0012`, including `0011` (pgvector) and `0012` (research lease and outbox).
- Task `2b7f69d15e5344c8bd029ee34f9bd0da` exited 0. Output: `0012 (head)`, heads `0012 (head)`, and a second `alembic upgrade head` with no further revisions.
- `GET /ready` reported `db_revision` `0012` and `migrations_current` true.
- Task environment has `DB_AUTO_MIGRATE=false`. No migration was edited. No new migration was added.

## G. Kafka validation

`python -m app.pipeline kafka init` from worker task `2ceb563c89164ff89c87d5f782f2ecba` exited 0. Bootstrap `10.42.10.160:9092`. All seven topics were `created` with `problems` empty and replication factor 1:

| Topic | Partitions | Retention hours |
| --- | --- | --- |
| `quantpilot.news.raw` | 3 | 168 |
| `quantpilot.market.raw` | 3 | 168 |
| `quantpilot.company.raw` | 1 | 168 |
| `quantpilot.ingestion.dlq` | 1 | 720 |
| `quantpilot.research.request` | 3 | 168 |
| `quantpilot.research.completed` | 3 | 168 |
| `quantpilot.research.dlq` | 1 | 720 |

The worker consumed `quantpilot.research.request`. A malformed produce was consumed without crashing the broker (the container stayed up). This is not Kafka high availability.

## H. S3 validation

Worker task `8a3d7e4cdd5a40df8e68190e0598b381` exited 0 using `build_object_store()` and `probe_write_once`:

- `backend=s3`, bucket `quantpilot-staging-lake-225201316318`, `endpoint_url=aws`, `versioning=Enabled`
- First write of `_health/write-once-probe.json` created the object
- Overwrite was refused
- Original bytes were intact
- `list("_health/")` returned that key

Bucket public access block: all four flags true. Encryption AES256. The bucket policy was not given a public grant.

## I. API health/readiness

`GET /health` returned HTTP 200:

```json
{"status":"ok","langsmith":false,"sentry":false,"otel":false,"version":"0.1.0","application":"ok","database":"ok","redis":"memory","draining":false}
```

`/health` does not check Kafka, the lake, or Neo4j.

`GET /ready` returned HTTP 200:

```json
{"status":"ok","application":"ok","database":"ok","db_revision":"0012","migrations_current":true,"redis":"memory","draining":false,"kafka":"ok","lake":"ok","neo4j":"unconfigured"}
```

Readiness fails closed on database, current migrations, Redis (`ok` or `memory`), and draining. Kafka, the lake, and Neo4j are reported and did not decide readiness. Neo4j is `unconfigured` because `neo4j_uri` is empty. That check was not weakened.

## J. Frontend validation

`GET /` on the ALB returned HTTP 200, `Content-Type: text/html`, `X-Powered-By: Next.js`. The HTML shell did not contain `localhost` or `127.0.0.1`. The client bundle `.next/static/chunks/716-64a561638d81e71d.js` contains `http://quantpilot-staging-1583627039.us-east-1.elb.amazonaws.com`.

`POST /api/research` with `Origin` set to that HTTP URL returned `access-control-allow-origin` equal to that origin and `access-control-allow-credentials: true`. Backend secret values are injected as ECS secrets, not as frontend build args.

## K. End-to-end research result

One request. Workload: `POST /api/research` query `What is the ticker symbol for Apple Inc?`, idempotency key `phase6-smoke-1`, correlation id `phase6-corr-1`. Provider: OpenAI (`gpt-4o-mini` in the worker log). Not FakeLLM. Empty staging corpus (no ingested company or news rows).

| Field | Value |
| --- | --- |
| HTTP | 202 in 29ms (`duration_ms` 29 on the API log) |
| `research_id` | `dee4e38d-5dd2-4843-b3d1-b34c1ac3c177` |
| `correlation_id` | `phase6-corr-1` |
| Accepted status | `publish_pending` |
| Final status | `partial` |
| `queued_at` | `2026-09-30T00:49:36.240975+00:00` |
| `started_at` | `2026-09-30T00:49:37.213293+00:00` |
| `finished_at` | `2026-09-30T00:50:06.126935+00:00` |
| `attempts` | 1 |
| Claims | 0 |
| Citations | 0 |
| Verification | supported 0, uncertain 0, unsupported 0, conflicting 0 |
| Limitations | `company unavailable`, `insufficient evidence for a verified factual section` |

Worker log: `RESEARCH_JOB_STARTED`, `EVIDENCE_PACKAGE_BUILT`, company agent `failed` after `duration_ms` 12049, model `gpt-4o-mini`, then `RESEARCH_RUN_FINISHED` status `partial`. The planner note was `planner output rejected; documented router used` and the selected agent was `company`. This is a functional pass of the queue and worker, not a supported-claim result. Do not read the elapsed time as a benchmark.

## L. Idempotency result

Same key `phase6-smoke-1` and the same query: HTTP 202, same `research_id` `dee4e38d-5dd2-4843-b3d1-b34c1ac3c177`, status `partial`, correlation id unchanged (`phase6-corr-1`).

Same key with query `What is the ticker symbol for Microsoft?`: HTTP 409, detail `Idempotency key 'phase6-smoke-1' was used for a different request`.

A following GET still returned the original query and status `partial`.

## M. Worker restart result

`stop-task` on `55074c7a6dc4445ab0da299eecf22e78` with reason `phase6 worker lease test`. A job submitted in the same step, `31345d2e-1bc4-4a92-a3a9-3613f17e219e`, started `2026-09-30T00:51:18.867152+00:00` and finished `succeeded` at `2026-09-30T00:51:30.233802+00:00` with `attempts` 1 and 0 claims. The task `stoppedAt` was `2026-09-29T20:52:24-04:00` (00:52:24Z), about 54 seconds after the job finished. Replacement task `800055123c9b46958ab060544a23bc45` started `2026-09-29T20:52:26-04:00`.

Lease reclaim was not observed. The in-flight job reached a terminal state during ECS's stop window, so a second claim set was not created because the job was already terminal. `RESEARCH_LEASE_SECONDS` remains 120. This does not demonstrate reclaim of a killed mid-flight job.

The earlier idle-exit (task exit 0 after 30 seconds with an empty topic) is the bug fixed by `idle_timeout_s=0`. After that image, the worker remained running across the smoke jobs.

## N. API restart result

`stop-task` on API task `d9392c4884954bdcb54acc46a82ca21a`. An 8-second poll then saw health HTTP 200 twice while `runningCount` was 0, then HTTP 503 for six samples, then `runningCount` 1 and health HTTP 200. Replacement task `aa3005d4b1044134b1bc95ef5b260d92`. The gap of 503 samples is about 48 seconds of that poll. Zero downtime was not measured.

After recovery, `GET /ready` was `ok` with revision `0012`, and `dee4e38d-5dd2-4843-b3d1-b34c1ac3c177` was still `partial` with correlation id `phase6-corr-1`.

## O. Outbox failure result

`docker stop kafka` on `i-0b4dc7cc8e237f7fa` left the container `Exited (143)`. `POST` idempotency key `phase6-outbox-1` returned 202 for `eae8673e-305a-4282-9cd5-ab02bf399770` at `publish_pending`. Eight seconds later it was still `publish_pending` with `attempts` 0.

`docker start kafka` reported `Up`. The next status read was `running` with `attempts` 1, then `partial`. The row was not dropped while the broker was stopped.

## P. DLQ result

A single payload `{not-json` was produced to `quantpilot.research.request`. The worker did not stay down (`runningCount` 1 after the test). The broker stayed `Up`. One DLQ record was read from `quantpilot.research.dlq`:

- `error_category`: `malformed`
- `error_message`: `Expecting property name enclosed in double quotes: line 1 column 2 (char 1)`
- `research_id`: null
- `correlation_id`: `unknown`
- `external_id`: `malformed`
- `attempt_count`: 1

The message has no secret material. It was not replayed.

## Q. Observability result

API logs and worker logs for the first job include `correlation_id` `phase6-corr-1` and the research id in `extra_data`. `OTEL_ENABLED` is false and `/health` reports `otel` false. No trace backend was configured, so no traces were verified.

`GET /metrics` on the ALB returned 1030 bytes of Prometheus text. Labels observed were `method`, `route`, and `status` on `quantpilot_http_requests_total` and `quantpilot_http_request_duration_seconds_*`, plus `quantpilot_graph_sync_pending{component="graph"}`. No series used `research_id`, `user_id`, `ticker`, or `cik`. Counts at that scrape: GET `/health` 93, GET `/api/research` 10, POST `/api/research` status 2xx 3 and 4xx 1.

The worker task has no published port, so worker counters and Kafka lag were not scraped from `/metrics`. Lag was not measured as a number.

## R. Security validation

- RDS not public: `PubliclyAccessible=false`, DNS `10.42.10.21`
- Kafka instance public IP null. Port 9092 is limited to the API and worker security groups
- S3 public access block all true
- Terraform outputs in this phase were endpoints and secret ARNs, not secret strings
- `scripts/scan-secrets.sh` printed `no secret markers`
- API image config had no key material (see E)
- Task roles are the per-service roles created by this stack, not an admin policy attached by this stack
- CORS echoed only the configured ALB origin
- Localhost is still rejected by production settings even when `ALLOW_HTTP_ORIGINS` is true

## S. HTTPS status

HTTPS remains pending ACM certificate configuration. `acm_certificate_arn` is empty. The load balancer has an HTTP listener on port 80 and no HTTPS listener. The frontend bundle uses the HTTP ALB URL. No certificate was created for this test.

## T. Backup/restore result

Method: `aws rds create-db-snapshot` on `quantpilot-staging`, snapshot id `quantpilot-staging-phase6`, started `2026-09-30T01:14:29Z`, status `available`, progress 100. Automated retention on the instance is 7 days. This is not an RPO.

Restore: `aws rds restore-db-instance-from-db-snapshot` into `quantpilot-staging-restore` at `2026-09-30T01:17:59Z`. Class `db.t4g.micro`, same subnet group, database security group, `PubliclyAccessible=false`, not Multi-AZ. A Fargate task using the existing database secret with only the hostname changed queried it at `2026-09-30T01:24:10Z` (about 6 minutes 11 seconds after the restore call). The instance status at the start of that task was `backing-up`.

| Check | Result |
| --- | --- |
| Alembic `version_num` | `0012` |
| `research_jobs` | 7 |
| `research_claims` | 0 |
| `reports` | 7 |
| Job `dee4e38d-5dd2-4843-b3d1-b34c1ac3c177` | `partial` |

Seven jobs matches the smoke set (one end-to-end, one worker-stop, one outbox, four matrix). Zero claims matches those jobs. This is not an RTO.

The disposable instance `quantpilot-staging-restore` was deleted after that query (`--skip-final-snapshot`). The snapshot `quantpilot-staging-phase6` was left in place. Delete it with `aws rds delete-db-snapshot --db-snapshot-identifier quantpilot-staging-phase6` when it is no longer needed.

## U. Small functional matrix result

After the first request, four more `POST /api/research` calls were accepted with HTTP 202. One worker. Real OpenAI, not FakeLLM. Staging corpus still empty. This is not the Phase 4.8 matrix and not a throughput result.

| Idempotency key | research_id | Final status | Attempts | Claims | Citations |
| --- | --- | --- | --- | --- | --- |
| `phase6-m1` | `383db909-f75a-4f10-9326-76ea3b986274` | succeeded | 1 | 0 | 0 |
| `phase6-m2` | `e1f7eebc-8677-4708-a6e4-e5f33d1fb491` | succeeded | 1 | 0 | 0 |
| `phase6-m3` | `1922c655-a280-4d77-b6ad-6985602abd20` | succeeded | 1 | 0 | 0 |
| `phase6-m4` | `3c7725cb-5095-407a-9565-b70bf1aa666a` | succeeded | 1 | 0 | 0 |

Each limitation list was `insufficient evidence for a verified factual section`. Verification counts were all zero. The master agent row recorded error `malformed` while the job status was `succeeded`. `phase6-m2` also ran the market agent (`succeeded`); the other three skipped news, company, technology, and market. No request failed to reach a terminal status.

## V. Cleanup status

The `quantpilot-staging` stack is still deployed and will keep incurring charges. Categories, not prices: NAT gateway, public IPv4, application load balancer, RDS `db.t4g.micro` single-AZ, Kafka `t3.small`, three Fargate tasks (API 0.5 vCPU/1 GB, worker 0.5 vCPU/1 GB, frontend 0.25 vCPU/0.5 GB), S3, CloudWatch logs, and the manual snapshot while it exists.

To remove only this stack, from a machine with the local state file:

```bash
terraform -chdir=infra/terraform destroy -var-file=environments/staging.tfvars
```

That state does not include the other VPC in this account. Do not destroy resources outside `quantpilot-staging`.

## W. Exact limitations

- Staging is not production. No production database, Kafka cluster, or bucket was targeted.
- RDS is single-AZ. Kafka replication factor is 1. Neither is high availability.
- The API stop produced a period of HTTP 503. Zero downtime was not measured. Automatic scaling was not tested.
- Phase 4.8 remains the only formal load matrix. These smoke calls are functional checks.
- No RPO or RTO.
- Neo4j was not configured, so graph projection was not part of the path. `/ready` reported `neo4j=unconfigured`.
- The staging lake and Postgres had no ingested market, news, or company corpus. Research jobs finished with zero claims and zero citations.
- The first research request finished `partial` because the company agent failed `malformed`. Four later requests finished `succeeded` with the same empty evidence limitation.
- Worker lease reclaim of a job killed mid-flight was not observed.
- HTTPS is not enabled.
- OpenTelemetry was off.
- Worker metrics and Kafka lag were not scraped.
- `deploy.yml` was not the apply path.
