# Phase 5 deployment

Production deployment configuration is in this repository. Phase 5 did not apply it. Phase 6 applied an isolated `quantpilot-staging` stack and recorded the measurements in [PHASE6_RESULTS.md](./PHASE6_RESULTS.md). That staging stack is not a production environment.

## Local development

Unchanged. `make api` uses development defaults, including SQLite and startup migrations. FakeLLM remains available when `LLM_PROVIDER=fake`.

## What Terraform creates

From `infra/terraform/`, with `environments/dev.tfvars.example`:

- One VPC, two public subnets, two private subnets, one NAT gateway, one S3 gateway endpoint.
- An internet-facing ALB. HTTP only, until `acm_certificate_arn` is set.
- ECS Fargate services for the API, the Next.js frontend, and the research worker. `start_services` defaults to false, so the first apply creates task definitions at desired count 0.
- RDS PostgreSQL 16, single-AZ, encrypted, not public, backups retained for `db_backup_retention_days` (example: 7). The master password is stored by RDS in Secrets Manager (`manage_master_user_password`).
- One EC2 instance running `apache/kafka:4.3.1` in KRaft mode, plaintext, replication factor 1, auto-create off.
- A private versioned S3 bucket.
- Empty Secrets Manager entries for `DATABASE_URL`, `OPENAI_API_KEY`, and `JWT_SECRET_KEY`. Terraform does not write the secret values.
- ECR repositories with scan-on-push. No image was pushed in this phase.
- A GitHub OIDC deploy role only when `github_repository` is set. The example leaves it empty.

Neo4j is not created. Spark, Airflow, and Grafana are not created.

## Order of operations

1. `terraform -chdir=infra/terraform init`
2. `terraform -chdir=infra/terraform plan -var-file=environments/dev.tfvars.example`
3. Apply only from the manual GitHub **Deploy** workflow, or locally if you intend to create the account resources. Copy the example tfvars to an untracked file before putting a real repository name or bucket name in it.
4. Build the RDS `DATABASE_URL` from the RDS endpoint and the RDS-managed secret. Store it with `aws secretsmanager put-secret-value`. Store the OpenAI key and JWT the same way. Do not commit them.
5. Build images and push to the ECR URLs in the Terraform outputs.

```bash
docker build --target app --build-arg REQUIREMENTS=requirements-pipeline.txt -t "$API_IMAGE" .
docker build --target app --build-arg REQUIREMENTS=requirements-pipeline.txt -t "$WORKER_IMAGE" .
docker build -f frontend/Dockerfile --build-arg NEXT_PUBLIC_API_URL="https://YOUR_ALB" -t "$WEB_IMAGE" frontend
```

`NEXT_PUBLIC_API_URL` is baked in at image build. Setting it on the running container does not change the browser bundle.

6. Run the migrate task (`alembic upgrade head`) or `make prod-migrate` with `DATABASE_URL` set. Alembic head is `0012`. Do not start the API before this.
7. Create Kafka topics with the existing command, from a one-off task or host that can reach the broker: `python -m app.pipeline kafka init`.
8. Set `start_services=true` and the image URIs, then apply again.

The API container listens with uvicorn, proxy headers enabled, as user `quantpilot`. The worker command is `python -m app.research worker`.

## CI

`.github/workflows/ci.yml` runs the existing backend and frontend jobs, then `terraform fmt -check`, `terraform init -backend=false`, `terraform validate`, and `scripts/scan-secrets.sh`.

`.github/workflows/deploy.yml` runs only on `workflow_dispatch`. `plan` is the default. `apply` is a second job that uses the GitHub environment named `production`. Protection rules for that environment are configured in GitHub, not in this file. The workflow assumes `vars.AWS_ROLE_ARN` and `vars.AWS_REGION` and uses OIDC. It does not store a long-lived AWS key.

## Rollback

Point the ECS service at the previous task definition and set the desired count. That does not roll back a database migration. A migration rollback is `alembic downgrade` and was not run against RDS in this phase. Do not downgrade `0012` as part of a normal deploy; no new migration was added.

## What Phase 5 did not execute

Phase 5 stopped at configuration. Phase 6 later ran `terraform apply`, image push, and staging smoke tests against `quantpilot-staging` only. See [PHASE6_RESULTS.md](./PHASE6_RESULTS.md).

Still not done:

- the GitHub `deploy.yml` workflow (Phase 6 used local Terraform against the staging state)
- an ACM certificate, so the staging listener is HTTP
- a container vulnerability scan beyond ECR `scan_on_push` being set
- any production-environment apply
