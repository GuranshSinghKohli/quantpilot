# `backend/app` — layers

Dependencies point downward. A package may import from layers below it, never above.

| Layer | Package | Owns | May import |
|-------|---------|------|------------|
| HTTP | `routers/` | Request parsing, auth dependency, response models | services, repositories, models, core |
| Orchestration | `services/`, `workflows/`, `agents/`, `jobs/` | Investigation runs, LangGraph graphs, schedulers | repositories, domain, db, models, core |
| Data access | `repositories/` | Idempotent upserts on core-domain tables | domain, core |
| Domain | `domain/` | Core tables (`models.py`), enums, identity/dedup rules, input schemas | core (db `Base` only) |
| Platform | `core/` | Typed settings, engine/session, Alembic runner, JSON logging | nothing in `app` |

Legacy packages still in use: `db/` holds the pre-Phase-1 ORM tables (users, investigations, evidence ledger), and `models/` holds the API Pydantic schemas. `config.py` is a flat shim over `core.settings` for modules that read module-level constants.

## Rules

- **Schema changes go through Alembic.** Edit a model in `domain/models.py` or `db/models.py`, then run `make revision m="..."` and review the generated file in `backend/alembic/versions/`. Nothing calls `create_all` at runtime. `tests/integration/test_migrations.py` fails if models and migrations drift.
- **Repositories never commit.** They `flush()` and return `(row, created)`, and the caller owns the transaction. Each write is keyed on the table's natural key (URL hash, ticker + exchange, event dedup key, or owner + idempotency key), so retries don't duplicate rows.
- **Shared knowledge vs owner-scoped work.** Sources, evidence, entities, events and relationships are global. Research jobs, agent runs, claims and reports carry `owner_key`.
- **Claims need evidence.** A `research_claims` row is FACT / OBSERVATION / INFERENCE / SCENARIO, and verification links it to `evidence_records` through `claim_evidence` (supports / contradicts / context).
- **Settings come from `core.settings.get_settings()`.** New code should not call `os.getenv`.
- **No trading advice.** Reports and agents never produce buy/hold/sell calls, price targets or position sizing.
