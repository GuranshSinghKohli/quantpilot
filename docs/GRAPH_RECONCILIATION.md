# Graph reconciliation

PostgreSQL is the source of truth. Neo4j is a derived projection. Reconciliation compares the two by stable IDs, never names.

## Ownership

| Store | Role |
|---|---|
| Postgres `entities`, `relationships` | Canonical graph |
| Postgres `graph_projections` | Last attempt per record |
| Postgres `graph_projection_runs` | One row per `project` / `repair` run |
| Neo4j | Optional query/index layer |

A Neo4j failure never rolls back PostgreSQL. Projection and repair are retryable.

## Reconcile (read-only)

```
python -m app.graph reconcile
make graph-reconcile
```

Example summary:

```
entities_postgres: 6
entities_neo4j: 6
missing_entities: 0
extra_entities: 0
relationships_postgres: 1
relationships_neo4j: 1
missing_relationships: 0
extra_relationships: 0
property_mismatches: 0
status: OK
```

`status` is `OK`, `DRIFT`, or `UNAVAILABLE` (Neo4j down — Postgres quality still exists). Extra Neo4j rows are reported only.

## Repair (opt-in)

```
python -m app.graph repair
python -m app.graph repair --allow-delete --confirm-delete
```

Default repair:

- upsert missing entities
- upsert missing relationships
- refresh mismatched properties

It does **not** delete extra Neo4j data. Deletion requires both `--allow-delete` and `--confirm-delete`. Default `project` stays additive.

Repair does not rewrite canonical Postgres entities or relationships. It only updates projection bookkeeping (`graph_projections`, `graph_projection_runs`).

## Test isolation

Developer Neo4j (`compose --profile graph`, `localhost:7687`, volume `quantpilot_neo4j`) is never used by tests.

Disposable Neo4j (`compose --profile graph-test`, `localhost:17687`, volume `quantpilot_neo4j_test`, password `quantpilot-test-only`) is wiped at session start and after each test.

`TEST_NEO4J_URI` is required. The suite fails closed on `bolt://localhost:7687` instead of falling back.

```
make graph-test-up
make test-neo4j
```

## Quality

```
python -m app.graph quality
```

Read-only report of entity, relationship, candidate, and projection hygiene.
