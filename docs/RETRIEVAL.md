# Retrieval (Phase 4.1)

`Retriever.search` combines structured filters and vector rank.

**Filters:** `entity_id` (via `document_entities`, resolver never guesses), `event_id` (document source = event primary source), `document_type`, `published_after` / `published_before`.

**Rank (v1):** cosine similarity of the configured embedding model. Ties: `document_id`, `chunk_index`. No freshness boost. Dates are filters and are returned on each hit.

**Dedup:** response-level, one chunk per document (highest score). The store still holds every chunk.

**Bounds:** `top_k` 1–`RETRIEVAL_MAX_TOP_K` (default 20).

**Failure:** if embeddings or pgvector are missing, `available=false` and `reason=semantic_unavailable`. Structured package sections still assemble from Postgres.

API: `GET /api/search`, `POST /api/research/context`. No raw SQL, Cypher, or embedding vectors.
