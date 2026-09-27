# Graph schema

PostgreSQL is the source of truth. Neo4j is a derived projection of `entities`, `entity_aliases`, `relationships`, and `relationship_evidence`.

## Ownership

| Concern | Store |
|---|---|
| Canonical company / technology / entity identity | Postgres `entities` + `companies` / `technologies` |
| Aliases (names, tickers) | Postgres `entity_aliases` |
| Directed relationships + provenance | Postgres `relationships` + `relationship_evidence` + `sources` + `evidence_records` |
| Last projection attempt | Postgres `graph_projections` |
| Projection run checkpoint | Postgres `graph_projection_runs` |
| Proposed edges (not graph edges) | Postgres `relationship_candidates` |
| Traversal (depth > 1) | Neo4j, if running |

There is no `graph_entities` / `graph_relationships` table. Those names would duplicate ADR-006.

## Postgres additions (migration `0008`)

- `entities.normalized_name` (NOT NULL), `source_system`, `source_key`
- Unique `(source_system, source_key)` when both are set
- Entity types: existing + `product`, `event`
- `relationships.provenance_kind` (`OBSERVED` | `VERIFIED` | `INFERRED`)
- `relationships` CHECK `valid_from <= valid_to`
- Relationship type `CUSTOMER_OF` (keeps `CUSTOMERS_OF`)
- `entity_aliases`, `graph_projections`

## Neo4j labels

`:Entity` on every node, plus one of `:Company` `:Technology` `:Product` `:Person` `:Organization` `:Event`.

Node properties: `entity_id` (identity), `entity_type`, `canonical_name`, `normalized_name`, `source_system`, `source_key`. Company nodes also have `cik`, `tickers`, `legal_name`, `exchange` when the SEC source provided them. `sic` / `country` / `industry` are not invented.

Relationship types match the Postgres vocabulary. Properties: `relationship_id` (identity), `confidence`, `source_id`, `evidence_id`, `valid_from`, `valid_to`, `provenance_kind`, `status`.

## Constraints / indexes

- `Entity.entity_id` unique
- `Entity.canonical_name` index
- `Entity.entity_type` index
- `Company.cik` index
- unique `relationship_id` per relationship type

Do not MERGE on names. Identity is `entity_id` / `relationship_id`.

## Provenance on a projected edge

`source_id` and `evidence_id` are copied from the first linked Postgres evidence row. An edge with no evidence is projected but `verified` is false.
