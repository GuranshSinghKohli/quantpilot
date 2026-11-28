# QuantPilot — Phase 4.1 Plan: Evidence, Document Retrieval & RAG Foundation

Status: **as-built with this step.**
Baseline: `3415330 feat: add market and event intelligence layer` (Alembic head `0010`).

Phase 4.1 adds a deterministic retrieval layer that future agents consume as an **Evidence Package**. It does **not** add LangGraph agents, LLM extraction, research reports, autonomous browsing, or investment advice.

```
sources / news_articles          events / impacts / relationships
        ↓                                      ↓
retrieval_documents                    structured facts
        ↓
deterministic chunks
        ↓
EmbeddingProvider (OpenAI or fake)
        ↓
chunk_embeddings (pgvector on Postgres; JSON on SQLite)
        ↓
hybrid Retriever + ContextAssembler
        ↓
EvidencePackage  →  future agents
```

PostgreSQL remains the system of record. pgvector is a derived retrieval index. Neo4j remains a graph projection and is not queried by this layer.

---

## Vocabulary

| Concept | Meaning | Store |
|---|---|---|
| **SOURCE** | Origin record (URL, publisher, lake pointer) | `sources` |
| **DOCUMENT** | Normalized retrieval document derived from a source | `retrieval_documents` |
| **EVIDENCE** | Immutable excerpt or structured fact | `evidence_records` (extended) |
| **CHUNK** | Deterministic retrieval unit of one document version | `retrieval_chunks` |
| **EMBEDDING** | Vector of one chunk for one model/version | `chunk_embeddings` |
| **RETRIEVAL RESULT** | Ranked chunk with provenance | not persisted |
| **EVIDENCE PACKAGE** | Bounded context for a research request | assembled, not a table |

These are not interchangeable. A news article is a source/document. An event is a normalized occurrence. An embedding is never the source of truth for text.

---

## Document model

Reuse `sources` + `news_articles`. Do not replace them.

`retrieval_documents` is 1:1 with `sources` (`source_id` unique). Identity is **not** title:

| Available | Identity |
|---|---|
| news `source_key` + `external_id` | `sha256(source_key\|external_id)` |
| otherwise | `sha256(url_hash)` |

`document_id` is `uuid.UUID` from the first 16 bytes of that digest.

**Content ownership.** Raw payloads stay in the lake (`sources.raw_uri`). Canonical news text used for retrieval is `title` + `summary` (the fields Postgres actually stores). Full article bodies are not invented. `normalized_text` and `content_hash` live on the retrieval document so tests and retrieval are deterministic without reading the lake. pgvector never owns document text.

**Types** (from `sources.source_type`; never claim a filing unless the source says so):

| source_type | document_type |
|---|---|
| news | news_article |
| filing | sec_filing |
| press_release | company_document |
| research | research_document |
| other | other |

If normalized text is empty, the source stays a source; no document is created.

**Versioning.** Same identity + same `content_hash` → skip. Content change increments `content_version`, stores `previous_content_hash`, writes new chunks. Old chunks remain (keyed by old `document_content_hash`) so historical evidence is not rewritten.

---

## Normalization

Deterministic, no LLM:

1. Unicode NFC
2. `\r\n` / `\r` → `\n`
3. Strip simple HTML tags (not a rewrite of wording)
4. Collapse 3+ newlines to 2; collapse horizontal whitespace per line
5. Strip ends

---

## Evidence

Reuse `evidence_records`. New optional `evidence_type` (default `document_excerpt`) and `document_id`. Records stay immutable: same `(source_id, content_hash)` is one row. A later document version does not mutate old excerpts.

Structured / market / graph facts in the package cite existing tables (`events`, `event_impacts`, `relationships` + `relationship_evidence`, `companies`). They are not fabricated quotes.

---

## Chunking (v1)

- Paragraph-aware (`\n\n+`)
- Target **800** characters, overlap **80**, drop pieces shorter than **40**
- Long paragraphs window-split with the same target/overlap
- Offsets are character offsets in `normalized_text`
- `chunk_id = sha256(document_id + document_content_hash + chunk_index)` (64-hex)
- Same document version always yields the same chunks

---

## Embeddings

`EmbeddingProvider`: `embed_texts`, `embed_query`, `dimension`, `model_name`, `model_version`.

| Setting | Default |
|---|---|
| `EMBEDDING_PROVIDER` | `openai` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` |
| `EMBEDDING_DIMENSION` | `1536` |
| `EMBEDDING_MODEL_VERSION` | `v1` |

OpenAI chat/completions are not used. Tests use `FakeEmbeddingProvider`: a deterministic lexical hashing trick (same text → same L2-normalized vector; overlapping tokens → similar vectors). It is **not** a semantic-quality claim.

Jobs: `python -m app.retrieval documents` then `python -m app.retrieval embed`. Skip when `(chunk_id, model, version)` and `content_hash` match. Transient provider errors retry with bounded backoff. Persistence is the commit point: a vector is not “embedded” until the row exists.

---

## pgvector

Migration `0011` runs `CREATE EXTENSION IF NOT EXISTS vector` on PostgreSQL only. Column `chunk_embeddings.embedding` is `vector(1536)` on Postgres and JSON on SQLite.

v1 uses **exact** cosine distance (`<=>` on Postgres; in-process cosine on SQLite). No HNSW/IVF. Appropriate for fixture-sized corpora. Changing dimension requires a new model version and a later migration.

If the extension or rows are missing: structured retrieval still works; the package marks `semantic` unavailable. The API stays up.

---

## Retrieval

`Retriever.search(query, filters, top_k)`:

1. Structured filters: entity (via `document_entities` + resolver), event (document source = event source), document_type, published_after/before
2. Vector rank among remaining chunks for the configured model/version
3. Score = cosine similarity
4. Ties: `document_id`, `chunk_index`
5. Response-level dedup: one chunk per document (highest score)
6. Bounded `top_k` (1–20)

No freshness boost in v1 (dates are filters and are exposed, not scored as “more accurate”).

---

## EvidencePackage

`ContextAssembler` is a deterministic service, not an agent. Limits (defaults): entities 20, events 20, graph 20, documents 10, chunks 10, excerpt 500. Oversized requests are rejected.

Citations point at real `source_id` / `document_id` / `chunk_id` / `evidence_id`. Graph edges without evidence are excluded. Unsupported items are omitted, never given fake URLs.

---

## Idempotency, failure, testing

- Document upsert and embed jobs are restartable and keyed.
- Embedding or pgvector failure cannot update `sources`, `news_articles`, market bars, or events.
- Neo4j is unused here; if it is down, structured retrieval is unchanged.
- Unit tests: SQLite + fake provider, no network.
- Vector operator tests: disposable Postgres + pgvector.
- Evaluation fixture is a **development set** with lexical-hash embeddings — not a published benchmark.

---

## Limitations

- News body is title+summary, not the lake raw payload
- No SEC scraping beyond existing source types
- No LLM query parsing
- No ChromaDB retirement (investigation memory still uses it)
- Fake embeddings are lexical, not semantic
- Exact search only; 1536-dim column is fixed for v1
