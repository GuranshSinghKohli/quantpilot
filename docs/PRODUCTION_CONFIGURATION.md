# Production configuration

`ENVIRONMENT=production` calls `Settings.validate_for_environment()` from the API lifespan and from `python -m app.research worker`. Development and tests do not use that gate.

A production process refuses to start when any of these are true:

- `JWT_SECRET_KEY` is empty or `dev-only-change-me-quantpilot`
- `DATABASE_URL` is SQLite, ends in `quantpilot.db`, or uses localhost
- `LLM_PROVIDER` or `EMBEDDING_PROVIDER` is `fake`
- `OPENAI_API_KEY` is empty while either provider is `openai`
- `KAFKA_BOOTSTRAP_SERVERS` uses localhost
- `LAKE_BACKEND` is not `s3`
- `LAKE_ENDPOINT_URL` is set (that path is for MinIO)
- `DB_AUTO_MIGRATE` is true
- `ALLOWED_ORIGINS` is empty, not `https`, or points at localhost

FakeLLM stays valid when `ENVIRONMENT` is not `production`.

## Variables the ECS task sets

| Variable | Required in production | Default in code | Secret | Consumer |
| --- | --- | --- | --- | --- |
| `ENVIRONMENT` | yes (`production`) | `development` | no | API, worker |
| `DATABASE_URL` | yes, Postgres | local SQLite file | yes | API, worker, migrate |
| `DB_AUTO_MIGRATE` | must be false | on only in development | no | API |
| `JWT_SECRET_KEY` | yes | dev placeholder | yes | API |
| `ALLOWED_ORIGINS` | yes, https | localhost:3000 | no | API CORS |
| `OPENAI_API_KEY` | yes with openai providers | empty | yes | API, worker |
| `LLM_PROVIDER` | `openai` | `openai` | no | worker, research |
| `EMBEDDING_PROVIDER` | `openai` | `openai` | no | retrieval |
| `LAKE_BACKEND` | `s3` | `s3` | no | lake |
| `LAKE_BUCKET` | yes | `quantpilot-lake` | no | lake |
| `LAKE_REGION` | yes | `us-east-1` | no | lake |
| `LAKE_ENDPOINT_URL` | must be empty | empty | no | lake |
| `KAFKA_BOOTSTRAP_SERVERS` | yes, private broker | `localhost:9092` | no | API readiness, worker |
| `KAFKA_REPLICATION_FACTOR` | `1` on this single node | `1` | no | topic init |
| `KAFKA_RESEARCH_PARTITIONS` | leave at 3 | `3` | no | topic init |
| `NEO4J_URI` | optional | empty | no | graph projection |
| `NEO4J_PASSWORD` | required only if `NEO4J_URI` is set | empty | yes | graph |
| `OTEL_ENABLED` | optional | false | no | API, worker |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | optional | empty | no | API, worker |
| `RESEARCH_LEASE_SECONDS` | optional | 120 | no | worker |
| `RESEARCH_HEARTBEAT_SECONDS` | optional | 15 | no | worker |
| `RESEARCH_MAX_ATTEMPTS` | optional | 3 | no | worker |
| `RESEARCH_MAX_QUEUED_JOBS` | optional | 100 | no | API submit |
| `NEXT_PUBLIC_API_URL` | yes at frontend image build | unset | no | browser only |

AWS credentials for S3 come from the task role. Do not set `AWS_ACCESS_KEY_ID` in production.

Other `Settings` fields (retrieval caps, investigation thresholds, Kafka topic names, embedding model) keep the defaults in `backend/app/core/settings.py`. Topic names are part of the contract and should stay at those defaults unless a deployment deliberately isolates them.

## Secret inventory

| Secret | Where it lives | Not in |
| --- | --- | --- |
| Postgres password | RDS-managed Secrets Manager secret | Git, images, Terraform source |
| `DATABASE_URL` | `${name}/database-url` | Git |
| `OPENAI_API_KEY` | `${name}/openai-api-key` | Git, frontend image |
| `JWT_SECRET_KEY` | `${name}/jwt-secret` | Git, logs (the settings repr hides `SecretStr`) |
| `NEO4J_PASSWORD` | created only when `neo4j_uri` is set | Git |

`.env.production.example` lists names only.
