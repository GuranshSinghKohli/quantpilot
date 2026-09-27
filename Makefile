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
        ingest-news ingest-market ingest-company sources-check \
        spark-build spark-news spark-market spark-company spark-all test-spark \
        load-news load-market load-company load-all \
        pipeline-status pipeline-check pipeline-reconcile pipeline-quality \
        retry-load reprocess-job retry-ingestion metrics-up metrics \
        graph-up graph-down graph-status graph-project graph-check graph-seed test-neo4j

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
	@echo "spark-build       build the spark + spark-test images (PySpark, Java 17, pinned S3A jars)"
	@echo "spark-news        raw → processed Parquet  [PROCESS_START= PROCESS_END= | PROCESS_PREFIX= | PROCESS_OBJECTS= ; PROCESS_OUTPUT_PREFIX=]"
	@echo "spark-market      same for market bars"
	@echo "spark-company     same for SEC company rows"
	@echo "spark-all         news, market, company  [PROCESS_START= PROCESS_END=]"
	@echo "test-spark        spark tier (real PySpark; MinIO via S3A) in the spark-test image"
	@echo "load-news         processed Parquet → Postgres for the latest COMPLETED news job  [LOAD_JOB= | LOAD_MANIFEST= ; LOAD_SINCE= LOAD_UNTIL= LOAD_BATCH_SIZE=]"
	@echo "load-market       same for market bars"
	@echo "load-company      same for SEC companies"
	@echo "load-all          latest COMPLETED job of each domain  [LOAD_SINCE= LOAD_UNTIL=]"
	@echo "pipeline-status   latest runs/jobs/loads, failures, Kafka lag, DLQ summary"
	@echo "pipeline-check    report stale RUNNING jobs (does not mark them failed)"
	@echo "pipeline-reconcile  scoped lineage/count/state check  [DOMAIN= JOB= SINCE= UNTIL=]"
	@echo "pipeline-quality  per-domain ingested → canonical counts  [DOMAIN=]"
	@echo "retry-load        load JOB= after verifying the manifest"
	@echo "reprocess-job     re-process JOB= from the manifest's raw keys"
	@echo "retry-ingestion   new ingest from FAILED/PARTIAL RUN="
	@echo "metrics           GET $(API_URL)/metrics"
	@echo "metrics-up        optional Prometheus scraping the API (compose profile observability)"
	@echo "graph-up          Neo4j (compose profile graph) then graph-status"
	@echo "graph-down        stop Neo4j (volume kept)"
	@echo "graph-status      Postgres vs Neo4j counts, last projection, schema"
	@echo "graph-project     idempotent Postgres → Neo4j upsert"
	@echo "graph-check       pending/failed projections (does not mutate)"
	@echo "graph-seed        deterministic evidenced relationship fixtures"
	@echo "test-neo4j        Neo4j tier (skip unless TEST_NEO4J_URI is set)"

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

# Spark processing runs in the one-shot `spark` service (needs `make pipeline-up`).
# Input: PROCESS_START/PROCESS_END (raw ingestion dates, default today UTC), or
# PROCESS_PREFIX, or PROCESS_OBJECTS (comma-separated raw keys). One of them only.
SPARK = $(PIPELINE) --profile spark run --rm --no-deps -e CODE_VERSION=$(CODE_VERSION) spark python -m app.pipeline
CODE_VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null)
PROCESS_OPTS = $(call opt,--start,$(PROCESS_START)) $(call opt,--end,$(PROCESS_END)) \
               $(call opt,--prefix,$(PROCESS_PREFIX)) $(call opt,--objects,$(PROCESS_OBJECTS)) \
               $(call opt,--output-prefix,$(PROCESS_OUTPUT_PREFIX))

spark-build:
	$(PIPELINE) --profile spark build spark spark-test

spark-news spark-market spark-company:
	$(SPARK) process $(subst spark-,,$@) $(PROCESS_OPTS)

spark-all:
	$(SPARK) process all $(call opt,--start,$(PROCESS_START)) $(call opt,--end,$(PROCESS_END))

test-spark:
	$(PIPELINE) --profile spark run --rm --no-deps spark-test

# Canonical loading runs in the one-shot `load` service (needs `make pipeline-up`).
# Selection: LOAD_JOB=<processing job id> or LOAD_MANIFEST=<manifest key/URI>;
# with neither, the latest COMPLETED processing job of the domain. Optional
# LOAD_SINCE/LOAD_UNTIL filter the manifest's partitions; LOAD_BATCH_SIZE (1000).
LOAD = $(PIPELINE) --profile load run --rm --no-deps load python -m app.pipeline load
LOAD_SELECT = $(if $(LOAD_JOB),--job $(LOAD_JOB),$(if $(LOAD_MANIFEST),--manifest $(LOAD_MANIFEST),--latest))
LOAD_OPTS = $(call opt,--since,$(LOAD_SINCE)) $(call opt,--until,$(LOAD_UNTIL)) \
            $(call opt,--batch-size,$(LOAD_BATCH_SIZE))

load-news load-market load-company:
	$(LOAD) --domain $(subst load-,,$@) $(LOAD_SELECT) $(LOAD_OPTS)

load-all:
	$(LOAD) --domain all --latest $(LOAD_OPTS)

OPS = $(PIPELINE) run --rm --no-deps lake-writer python -m app.pipeline

pipeline-status:
	$(OPS) status

pipeline-check:
	$(OPS) check

pipeline-reconcile:
	$(OPS) reconcile $(call opt,--domain,$(DOMAIN)) $(call opt,--job,$(JOB)) \
		$(call opt,--since,$(SINCE)) $(call opt,--until,$(UNTIL))

pipeline-quality:
	$(OPS) quality $(call opt,--domain,$(DOMAIN))

retry-load:
	@test -n "$(JOB)" || (echo 'usage: make retry-load JOB=<processing job id>' && exit 1)
	$(OPS) retry-load $(JOB)

reprocess-job:
	@test -n "$(JOB)" || (echo 'usage: make reprocess-job JOB=<processing job id>' && exit 1)
	$(PIPELINE) --profile spark run --rm --no-deps spark python -m app.pipeline reprocess-job $(JOB)

retry-ingestion:
	@test -n "$(RUN)" || (echo 'usage: make retry-ingestion RUN=<ingestion run id>' && exit 1)
	$(INGEST) retry-ingestion $(RUN)

metrics:
	@curl -fsS $(API_URL)/metrics; echo

metrics-up:
	$(COMPOSE) --profile observability up -d prometheus

NEO4J_PASSWORD ?= quantpilot-dev-only
NEO4J_BOLT_PORT ?= 7687
TEST_NEO4J_URI ?= bolt://localhost:$(NEO4J_BOLT_PORT)

graph-up:
	NEO4J_PASSWORD=$(NEO4J_PASSWORD) NEO4J_BOLT_PORT=$(NEO4J_BOLT_PORT) \
		$(COMPOSE) --profile graph up -d --wait neo4j
	$(MAKE) graph-status

graph-down:
	$(COMPOSE) --profile graph stop neo4j

graph-status:
	cd backend && NEO4J_URI=bolt://localhost:$(NEO4J_BOLT_PORT) NEO4J_PASSWORD=$(NEO4J_PASSWORD) \
		../$(PY) -m app.graph status

graph-project:
	cd backend && NEO4J_URI=bolt://localhost:$(NEO4J_BOLT_PORT) NEO4J_PASSWORD=$(NEO4J_PASSWORD) \
		../$(PY) -m app.graph project

graph-check:
	cd backend && NEO4J_URI=bolt://localhost:$(NEO4J_BOLT_PORT) NEO4J_PASSWORD=$(NEO4J_PASSWORD) \
		../$(PY) -m app.graph check

graph-seed:
	cd backend && ../$(PY) -m app.graph seed-fixtures

test-neo4j:
	cd backend && TEST_NEO4J_URI=$(TEST_NEO4J_URI) NEO4J_URI=$(TEST_NEO4J_URI) \
		NEO4J_PASSWORD=$(NEO4J_PASSWORD) ../$(PY) -m pytest tests/neo4j -q
