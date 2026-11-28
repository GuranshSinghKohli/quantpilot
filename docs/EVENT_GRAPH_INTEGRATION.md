# Event graph integration

PostgreSQL remains authoritative. Neo4j is a derived projection.

## Vocabulary

| Postgres | Neo4j |
|---|---|
| Confirmed `events` | `:CanonicalEvent {event_id}` |
| Confirmed `event_entities` | `(:CanonicalEvent)-[:INVOLVES {link_id, role}]->(:Entity)` |

`:CanonicalEvent` is not `:Entity:Event`. Entity-type `event` nodes stay on the entity supertable. `INVOLVES` is graph-only and is **not** a `relationships.relationship_type`.

Only `events.status=confirmed` associations are projected. Rejected / pending candidates never appear.

## Projection

`python -m app.graph project` MERGEs event nodes and INVOLVES edges after entities. Idempotent. Additive by default.

`graph_projections.record_kind` may be `entity`, `relationship`, `event`, or `event_link`.

## Reconciliation

`python -m app.graph reconcile` now also reports:

- missing / extra CanonicalEvent nodes
- missing / extra INVOLVES edges

Repair upserts missing event nodes/links. Deleting extras still requires `--allow-delete --confirm-delete`.
