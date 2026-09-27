# Entity resolution

Conservative and deterministic. If two candidates match, the resolver returns `AMBIGUOUS` and does not guess.

## Statuses

| Status | Meaning |
|---|---|
| `RESOLVED` | Exactly one entity |
| `UNRESOLVED` | Zero entities |
| `AMBIGUOUS` | Two or more entities |

## Match order

1. `entity_id` if supplied
2. Company CIK (`companies.cik`, zero-padded to 10 digits)
3. `(source_system, source_key)` — SEC companies use `sec` + CIK
4. Exact `normalized_name` or exact `LEGAL_NAME` / `DISPLAY_NAME` alias
5. Exact `TICKER` alias (or `companies.ticker`)

Normalization for names: Unicode NFKC, casefold, strip punctuation, collapse whitespace. `"Meta Platforms, Inc."` and `"Meta Platforms Inc"` match. `"Meta"` and `"Meta Platforms"` do not.

Tickers are uppercased. They are aliases, not identity. Two entities with the same ticker alias → `AMBIGUOUS`.

## What is never done

- Fuzzy / edit-distance / embedding similarity
- Automatic merge because two names look alike
- Creating a relationship because two entities appear in the same document

Co-mention is not evidence of `PARTNERED_WITH`, `INVESTED_IN`, or any other type. The relationship service requires an explicit type plus `source_id` and `evidence_id`.
