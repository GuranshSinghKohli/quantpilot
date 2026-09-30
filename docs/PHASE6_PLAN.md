# Phase 6 plan

Validate the Phase 5 deployment configuration by running it as an isolated AWS staging system. The question is whether the existing architecture operates when the processes are no longer on one machine.

This phase does not add agents, browsing, recommendations, or investment advice. It does not rerun the Phase 4.8 load matrix. It does not deploy to a production environment.

## Isolation

The stack name is `quantpilot-staging`. Terraform state for this stack is local and gitignored. Plans are reviewed before apply. A plan that destroys or changes resources outside this stack is a stop.

`ENVIRONMENT=production` on the tasks is the application gate (real Postgres, real OpenAI, no startup migrations). `environment=staging` is only the AWS name prefix.

## Sequence

1. Inspect Terraform, workflows, Dockerfiles, and production settings. Fix only blockers that stop a staging apply.
2. `terraform plan`, then apply the isolated stack.
3. Build the existing API, worker, and frontend images. Push immutable tags. Do not use `latest` as the only tag.
4. Run `alembic upgrade head` as a one-shot ECS task. The API does not migrate on startup. Expected head: `0012`.
5. `python -m app.pipeline kafka init`. Confirm the seven existing topics. Replication factor 1 stays.
6. Exercise the private S3 lake with the existing write-once store.
7. Check `GET /health` and `GET /ready` without weakening readiness.
8. Load the frontend and confirm the baked API URL.
9. One research request, then an idempotency pair, a worker stop, an API stop, a Kafka stop for the outbox, and one malformed message to the research DLQ.
10. A four-request functional matrix on the configured OpenAI provider. Not a capacity test.
11. A staging RDS snapshot, and a restore into a disposable database if that completes.
12. Document only what was measured.

HTTPS stays off unless a real ACM certificate is supplied.
