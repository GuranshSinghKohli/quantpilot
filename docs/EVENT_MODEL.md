# Event model

An **event** is a normalized occurrence. A **news article** is a source/document. Not every article is an event.

## Tables

| Table | Meaning |
|---|---|
| `sources` / `news_articles` | Documents |
| `event_candidates` | Proposed occurrences (`PENDING` / `ACCEPTED` / `REJECTED` / `AMBIGUOUS`) |
| `events` | Canonical occurrences |
| `event_entities` | Event ↔ entity roles |
| `event_impacts` | Descriptive market windows (confirmed events only) |

## Identity

Do not key on title.

| Path | Key |
|---|---|
| `upsert_event` | `sha256(type \| subject entity \| UTC day \| source url_hash)` |
| Source-backed candidate | `sha256(source_key \| external_id \| event_type)` |
| Evidence-backed candidate | `sha256(source_id \| evidence_id \| event_type)` |

Repeated propose/accept is idempotent on that key.

## Types (stored lowercase)

`news`, `earnings`, `acquisition`, `investment`, `partnership`, `product_launch`, `technology_release`, `executive_change`, `regulatory`, `other`, plus existing `funding`, `filing`, `market_move`.

`NEWS` from a vendor article means “document with structured tickers,” not a classified economic event.

## Roles (stored lowercase)

`subject`, `counterparty`, `target`, `acquirer`, `acquired`, `investor`, `partner`, `supplier`, `customer`, `technology`, `mentioned`, `other`.

Ticker tags: first resolved ticker → `subject`; additional resolved tickers → `other`. Unresolved / ambiguous tickers are not guessed. Co-mention is not a role.

## Acceptance

Candidates do not enter impact math or Neo4j until `ACCEPTED` → `events.status=confirmed`.
