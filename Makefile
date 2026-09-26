# QuantPilot developer commands. Run from the repo root.
# Backend venv: `cd backend && uv venv --python 3.11 && uv pip install -r requirements-dev.txt`

PY        ?= backend/.venv/bin/python
ALEMBIC   ?= backend/.venv/bin/alembic
COMPOSE   ?= docker compose
TEST_DATABASE_URL ?= postgresql://quantpilot:quantpilot@localhost:5432/quantpilot_test
API_URL   ?= http://localhost:8000

.PHONY: help up dev down db migrate revision api test test-unit test-integration typecheck ready

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
