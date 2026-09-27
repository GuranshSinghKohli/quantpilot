# Entity resolution

Conservative and deterministic. If two candidates match, the resolver returns `AMBIGUOUS` and does not guess.

## Statuses

| Status | Meaning |
|---|---|
| `RESOLVED` | Exactly one entity |
| `UNRESOLVED` | Zero entities |
| `AMBIGUOUS` | Two or more entities |

Public result:

```
status, entity_id, matched_by, candidates
```

`candidates` is a list of `entity_id` values only. Tickers, CIKs, names, and URLs are not returned as identifiers in metrics or logs.

## Match order

1. Exact `entity_id` if supplied
2. Exact authoritative identifier — company CIK (`companies.cik`, zero-padded to 10 digits)
3. Exact source identity — `(source_system, source_key)` (SEC: `sec` + CIK)
4. Exact `normalized_name` or exact `LEGAL_NAME` / `DISPLAY_NAME` alias (includes the legal-name stem)
5. Exact `TICKER` alias (or `companies.ticker`)

No fuzzy / edit-distance / embedding similarity. No automatic merge because two names look alike.

Normalization for names: Unicode NFKC, casefold, strip punctuation, collapse whitespace. `"Meta Platforms, Inc."` and `"Meta Platforms Inc"` match. `"Meta"` and `"Meta Platforms"` do not.

Tickers are uppercased. They are aliases, not identity. Two entities with the same ticker alias → `AMBIGUOUS`. A ticker is not assumed to identify one company across all history.

## Aliases

Idempotent inserts from authoritative structured fields:

| Type | Source |
|---|---|
| `LEGAL_NAME` | `companies.legal_name` |
| `DISPLAY_NAME` | canonical / display name, plus a legal-stem (`NVIDIA CORPORATION` → `NVIDIA`) |
| `TICKER` | `ticker` and `tickers` |
| `SOURCE_ALIAS` | a distinct source-provided name, if present |

Tickers never become the primary key. CIK / `(source_system, source_key)` remain identity.

## Enrichment

SEC company_tickers_exchange fields only: `cik`, `name`, `ticker`, `exchange`.

Projected company properties: `cik`, `tickers`, `exchange`, `legal_name`, `source_system`, `source_key`.

**Not invented:** `sic`, `country`, `industry`.

## What is never done

- Fuzzy matching
- Silent pick among ambiguous hits
- Creating a relationship because two entities appear in the same document

Co-mention is not evidence of `PARTNERED_WITH`, `INVESTED_IN`, or any other type. Candidates and the relationship service both require an explicit type plus `source_id` and `evidence_id`.
