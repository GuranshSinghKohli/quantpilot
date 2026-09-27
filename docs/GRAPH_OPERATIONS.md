# Graph operations

Neo4j is optional. Postgres writes never participate in a distributed transaction with Neo4j.

## Configuration

| Variable | Role |
|---|---|
| `NEO4J_URI` | Empty = disabled. Otherwise `bolt://` or `neo4j://` |
| `NEO4J_USER` | Default `neo4j` |
| `NEO4J_PASSWORD` | `SecretStr`; never logged |
| `NEO4J_DATABASE` | Default `neo4j` |

Compose profile `graph` starts Neo4j 5 community. `make up` does not start it.

## Commands

```
make graph-up          # start Neo4j
make graph-status      # python -m app.graph status
make graph-project     # idempotent Postgres → Neo4j
make graph-check       # pending / failed (no mutation)
make graph-seed        # evidenced fixtures only
make test-neo4j        # requires TEST_NEO4J_URI
```

`project` twice must not duplicate nodes or relationships (MERGE on `entity_id` / `relationship_id`).

If Neo4j is down: `project` exits 2, Postgres is unchanged, `/ready` stays ok and reports `neo4j=unavailable`. Restart Neo4j and rerun `project`.

## API (read-only)

- `GET /api/entities/{entity_id}`
- `GET /api/entities/{entity_id}/relationships`
- `GET /api/entities/{entity_id}/neighbors?depth=1`
- `GET /api/graph/relationship?from_entity_id=&to_entity_id=`

Depth is 1–3. Depth 1 is Postgres. Depth > 1 needs Neo4j (503 if down). No client Cypher. Results are capped at 50.

## Metrics

`graph_projection_total`, `graph_projection_duration_seconds`, `graph_projection_failures_total`, `graph_entities_projected_total`, `graph_relationships_projected_total`, `graph_sync_pending`.

Labels are only `component`, `operation`, `status`, `kind`. Never `entity_id`, `relationship_id`, ticker, CIK, URL, or name.

## Recovery

1. Confirm Postgres (`/ready`, `make graph-status`).
2. Start Neo4j (`make graph-up`).
3. `make graph-project` (safe to rerun).
4. `make graph-check`.

Do not delete raw lake data. Do not auto-replay DLQ. Do not auto-fail stuck pipeline jobs.

## Retention

Graph projections are a log of last attempt, not a second warehouse. Keep Postgres entities/relationships with the canonical tables. Neo4j can be wiped and rebuilt from Postgres.
