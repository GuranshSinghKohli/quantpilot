# QuantPilot developer commands. Run from the repo root.
# Backend venv: `cd backend && uv venv --python 3.11 && uv pip install -r requirements-dev.txt`

PY        ?= backend/.venv/bin/python
ALEMBIC   ?= backend/.venv/bin/alembic
COMPOSE   ?= docker compose
TEST_DATABASE_URL ?= postgresql://quantpilot:quantpilot@localhost:5432/quantpilot_test
API_URL   ?= http://localhost:8000

# Local MinIO (compose profile "pipeline"). Dev-only placeholders matching
# docker-compose.yml; the host process gets them as standard AWS_* variables.
MINIO_PORT          ?= 9000
MINIO_ROOT_USER     ?= quantpilot
MINIO_ROOT_PASSWORD ?= quantpilot-dev-only
KAFKA_PORT          ?= 9092
POSTGRES_PORT       ?= 5432
PIPELINE = MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
           MINIO_PORT=$(MINIO_PORT) KAFKA_PORT=$(KAFKA_PORT) POSTGRES_PORT=$(POSTGRES_PORT) \
           $(COMPOSE) --profile pipeline
INGEST = $(PIPELINE) --profile ingest run --rm --no-deps ingest python -m app.pipeline
opt = $(if $(2),$(1) $(2))
LIMIT_OPTS = $(call opt,--max-records,$(MAX_RECORDS)) $(call opt,--page-size,$(PAGE_SIZE)) \
             $(call opt,--max-pages,$(MAX_PAGES))
LAKE_ENV = LAKE_BACKEND=s3 LAKE_ENDPOINT_URL=http://localhost:$(MINIO_PORT) \
           AWS_ACCESS_KEY_ID=$(MINIO_ROOT_USER) AWS_SECRET_ACCESS_KEY=$(MINIO_ROOT_PASSWORD)

.PHONY: help up dev down db migrate revision api test test-unit test-integration typecheck ready \
        lake-up lake-down lake-init lake-check test-s3 \
        kafka-up kafka-down kafka-check pipeline-up pipeline-down pipeline-logs publish-synthetic dlq test-kafka \
        ingest-news ingest-market ingest-company sources-check

help:
	@echo "up                compose stack (postgres, redis, migrate, backend, frontend)"
	@echo "dev               compose stack with API hot reload"
	@echo "down              stop the stack (volumes kept)"
	@echo "db                start only postgres + redis"
	@echo "migrate           alembic upgrade head against DATABASE_URL (default: local SQLite)"
	@echo "revision m=msg    autogenerate a migration from app/domain + app/db models"
	@echo "api               run the API on the host with reload"
	@echo "test              full backend suite (SQLite; integration tier skipped)"
	@echo "test-unit         backend/tests/unit only"
	@echo "test-integration  Postgres tier; needs 'make db' (TEST_DATABASE_URL=$(TEST_DATABASE_URL))"
	@echo "typecheck         frontend tsc --noEmit"
	@echo "ready             GET $(API_URL)/ready"
	@echo "lake-up           MinIO + bucket (versioned), then lake-check"
	@echo "lake-down         stop MinIO (volume kept)"
	@echo "lake-init         create bucket + enable versioning via boto3 (idempotent)"
	@echo "lake-check        bucket, versioning and a write-once probe against MinIO"
	@echo "test-s3           S3 tier against MinIO (throwaway quantpilot-test-* buckets)"
	@echo "kafka-up          Kafka (KRaft) + kafka-init (topics from Settings), then kafka-check"
	@echo "kafka-down        stop Kafka (volume kept)"
	@echo "kafka-check       verify topics and show lake-writer consumer-group lag"
	@echo "pipeline-up       postgres + migrate + MinIO + Kafka + lake-writer (compose profile pipeline)"
	@echo "pipeline-down     stop the pipeline services"
	@echo "publish-synthetic n=5  publish SYNTHETIC market events through the tracked publisher (compose Postgres)"
	@echo "dlq               list dead letters (never commits)"
	@echo "test-kafka        kafka tier (+ mocked-provider e2e into MinIO) against the compose broker"
	@echo "ingest-news       Massive news   [NEWS_TICKERS= NEWS_START= NEWS_END= MAX_RECORDS=]"
	@echo "ingest-market     Massive bars   [MARKET_SYMBOLS= MARKET_START= MARKET_END= MAX_RECORDS=]"
	@echo "ingest-company    SEC companies  [COMPANY_SYMBOLS= | COMPANY_ALL=1 MAX_RECORDS=]"
	@echo "sources-check     provider health: configured / reachable / auth (not part of API /health)"

up:
	$(COMPOSE) up --build

dev:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	$(COMPOSE) down

db:
	$(COMPOSE) up -d postgres redis

migrate:
	cd backend && ../$(ALEMBIC) upgrade head

revision:
	@test -n "$(m)" || (echo 'usage: make revision m="describe change"' && exit 1)
	cd backend && ../$(ALEMBIC) revision --autogenerate -m "$(m)"

api:
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	cd backend && ../$(PY) -m pytest tests -q

test-unit:
	cd backend && ../$(PY) -m pytest tests/unit -q

test-integration:
	cd backend && TEST_DATABASE_URL=$(TEST_DATABASE_URL) ../$(PY) -m pytest tests/integration -q

typecheck:
	cd frontend && npx tsc --noEmit

ready:
	@curl -fsS $(API_URL)/ready; echo

lake-up:
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) MINIO_PORT=$(MINIO_PORT) \
		$(COMPOSE) --profile pipeline run --rm minio-init
	$(MAKE) lake-check

lake-down:
	$(COMPOSE) --profile pipeline stop minio

lake-init:
	cd backend && $(LAKE_ENV) ../$(PY) -m app.pipeline lake init

lake-check:
	cd backend && $(LAKE_ENV) ../$(PY) -m app.pipeline lake check

test-s3:
	cd backend && TEST_S3_ENDPOINT=http://localhost:$(MINIO_PORT) \
		TEST_S3_ACCESS_KEY_ID=$(MINIO_ROOT_USER) TEST_S3_SECRET_ACCESS_KEY=$(MINIO_ROOT_PASSWORD) \
		../$(PY) -m pytest tests/s3 -q

kafka-up:
	$(PIPELINE) up -d --wait kafka
	$(PIPELINE) run --rm kafka-init
	$(MAKE) kafka-check

kafka-down:
	$(PIPELINE) stop kafka

kafka-check:
	$(PIPELINE) run --rm --no-deps kafka-init python -m app.pipeline kafka check

pipeline-up:
	$(PIPELINE) up -d --build --wait lake-writer

pipeline-down:
	$(PIPELINE) stop lake-writer kafka minio postgres redis

pipeline-logs:
	$(PIPELINE) logs -f lake-writer

publish-synthetic:
	$(PIPELINE) run --rm --no-deps lake-writer python -m app.pipeline publish-synthetic --count $(or $(n),5)

dlq:
	$(PIPELINE) run --rm --no-deps lake-writer python -m app.pipeline dlq

test-kafka:
	cd backend && TEST_KAFKA_BOOTSTRAP=localhost:$(KAFKA_PORT) TEST_S3_ENDPOINT=http://localhost:$(MINIO_PORT) \
		TEST_S3_ACCESS_KEY_ID=$(MINIO_ROOT_USER) TEST_S3_SECRET_ACCESS_KEY=$(MINIO_ROOT_PASSWORD) \
		../$(PY) -m pytest tests/kafka -q

# Source ingestion runs in the one-shot `ingest` service (needs `make pipeline-up`).
# Each variable is optional; unset means the Settings default (see ENV_VARIABLES.md).
ingest-news:
	$(INGEST) ingest news $(call opt,--symbols,$(NEWS_TICKERS)) $(call opt,--start,$(NEWS_START)) \
		$(call opt,--end,$(NEWS_END)) $(LIMIT_OPTS)

ingest-market:
	$(INGEST) ingest market $(call opt,--symbols,$(MARKET_SYMBOLS)) $(call opt,--start,$(MARKET_START)) \
		$(call opt,--end,$(MARKET_END)) $(LIMIT_OPTS)

ingest-company:
	$(INGEST) ingest company $(if $(COMPANY_ALL),--all,$(call opt,--symbols,$(COMPANY_SYMBOLS))) $(LIMIT_OPTS)

sources-check:
	$(INGEST) sources check
