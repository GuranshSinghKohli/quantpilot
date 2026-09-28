# QuantPilot — Phase 3.3 Plan: Market & Event Intelligence

Status: **as-built with this step.**
Baseline: `b9036ed feat: harden graph reliability and relationship discovery` (Alembic head `0009`).

Phase 3.3 connects canonical news/market data and the entity graph into a **descriptive** event/market intelligence layer. It does **not** add LangGraph, RAG, pgvector, LLM extraction, causal inference, or investment advice.

```
Canonical news / sources          Canonical market_bars
        ↓                                  ↓
Event candidates (PENDING)           Trading-session calendar
        ↓ accept
events + event_entities
        ↓
event_impacts (v1)
        ↓
Neo4j :CanonicalEvent -[:INVOLVES]-> :Entity
```

---

## 1. Event model

**Reuse `events` and `event_entities`.** No second `graph_events` table.

| Field | Store |
|---|---|
| `event_id` | `events.id` |
| `event_type` | existing + `news`, `technology_release` |
| `title` / `summary` | title + description |
| `occurred_at` | when the occurrence happened (nullable) |
| `published_at` | when the source published (new, nullable) |
| `source_id` | `primary_source_id` |
| `evidence_id` | new nullable FK |
| `status` | `detected` / `confirmed` / `rejected` — accepted candidates become `confirmed` |

A **news article** (`news_articles` + `sources`) is a document. An **event** is a normalized occurrence. Not every article becomes an event.

`market_event_windows` (Phase 1 placeholder, one row per window) is **not** written by this phase. The intelligence record is `event_impacts`.

---

## 2. Event identity

Do not key on title alone.

| Path | Identity |
|---|---|
| Existing `upsert_event` | `event_dedup_key(type, subject entity, UTC day, source url_hash)` |
| Source-backed candidate | `sha256(source_key \| external_id \| event_type)` when the vendor id exists |
| Explicit proposal | `sha256(source_id \| evidence_id \| event_type)` |

Repeated propose/accept is idempotent on that key.

---

## 3. Candidates

Table `event_candidates`. Statuses: `PENDING` / `ACCEPTED` / `REJECTED` / `AMBIGUOUS`.

News with **structured tickers** may become a `NEWS` candidate. Articles with no tickers stay documents. Candidates never enter impact math or Neo4j until `ACCEPTED` → `events.status=confirmed`.

---

## 4. Entity association

Reuse `event_entities`. New optional `source_id`, `evidence_id`, `confidence`. Roles (stored lowercase): existing `subject` / `counterparty` / `mentioned` plus `target`, `acquirer`, `acquired`, `investor`, `partner`, `supplier`, `customer`, `technology`, `other`.

Ticker tags from a news vendor list resolve via Phase 3.2 `resolve_entity`. First resolved ticker → `subject`; additional resolved tickers → `other`. Unresolved / ambiguous tickers are **not** guessed and do not get a row. Co-mention is not a role.

---

## 5. Market windows

Reuse `market_bars` (`symbol`, `trading_date`, `open`/`high`/`low`/`close`, `volume`). **v1 uses `close`, not `adj_close`.** Currency and dividend adjustment are not applied.

Trading days = distinct `trading_date` values that exist for that symbol (and separately for the benchmark). Windows `-1D/+1D/+3D/+5D/+20D` are session offsets, not calendar days.

**Event trading date T0**

1. Timestamp = `occurred_at` if set, else `published_at`.
2. Calendar date = that timestamp in UTC (no invented exchange TZ).
3. If a bar exists on that date → T0.
4. Else T0 = the next later session (weekend/holiday).
5. After-hours is not distinguished: daily bars have no session clock.

Publication time is not pretended to be occurrence time when only `published_at` is set.

---

## 6. Methodology (calculation_version = `v1`)

| Metric | Formula |
|---|---|
| `return_pN` | `close(T+N) / close(T0) - 1` |
| `return_m1` | `close(T0) / close(T-1) - 1` (move into the event session) |
| `relative_return` | asset return − benchmark return (same window) |
| Simple abnormal-return proxy | same as `relative_return` — **not** CAPM, **not** causal |
| `volume_ratio` | event-day volume / mean volume of 20 prior sessions |
| `pre_event_volatility` | sample stdev of daily close-to-close returns over 20 prior sessions |
| `event_window_volatility` | sample stdev of daily returns from T0 through T+5 (inclusive of those intervals) |

Missing bars → `NULL` (unavailable). No forward-fill. Invalid OHLC is not coerced to zero.

Benchmark symbol: `MARKET_BENCHMARK_SYMBOL` (default `SPY`). If that series is missing, benchmark fields stay null. No silent substitute.

---

## 7. Storage / lineage / idempotency

`event_impacts` unique on `(event_id, entity_id, symbol, calculation_version)`.

Lineage: `event_id`, `entity_id`, `first_market_date`, `last_market_date`, `calculation_job_id`, `calculation_version`. Market payloads stay in `market_bars`.

Re-run of `v1` updates the same row. A future `v2` is a new row.

---

## 8. Graph

`:CanonicalEvent {event_id}` (avoids clashing with `Entity:Event`).  
`[:INVOLVES {role, link_id}]` to `:Entity`. Only `confirmed` events and their associations. Additive projection. Reconcile reports missing/extra event nodes and INVOLVES edges.

---

## 9. Failure / recovery

Calculator never writes `market_bars` or canonical companies. Neo4j down → Postgres impacts still persist; projection exits 2. Batch reports inserted/updated/skipped/unavailable/failed.

---

## 10. Commands

```
python -m app.market events-from-news
python -m app.market impact [EVENT_ID] [--since DATE] [--until DATE]
python -m app.market quality
```

---

## 11. Testing

Unit fixtures only (no network). Isolated Neo4j for event projection. Host + spark-test full suites. Dev SQLite pinned by `tests/conftest.py`.

---

## 12. Limitations

- Daily bars only; no intraday or after-close split.
- UTC date, not a full exchange calendar file.
- Unadjusted `close`.
- `NEWS` candidates are document+ticker tags, not classified economic events.
- No LLM type/role extraction.
- Benchmark-adjusted return is not attribution.
