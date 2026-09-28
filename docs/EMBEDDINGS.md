# Embeddings (Phase 4.1)

`EmbeddingProvider` exposes `embed_texts`, `embed_query`, `dimension`, `model_name`, `model_version`.

| Setting | Default |
|---|---|
| `EMBEDDING_PROVIDER` | `openai` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` |
| `EMBEDDING_DIMENSION` | `1536` |
| `EMBEDDING_MODEL_VERSION` | `v1` |

OpenAI chat APIs are not used. Tests and `python -m app.retrieval evaluate` use `FakeEmbeddingProvider` (lexical hashing trick: same text → same L2-normalized vector). That is not a semantic-quality claim.

Jobs: `python -m app.retrieval documents` then `embed`. Skip when chunk content hash + model + version match. Transient errors retry with bounded backoff. A chunk is embedded only after the row is persisted.

PostgreSQL stores `vector(1536)` via pgvector (migration `0011`). SQLite stores JSON. v1 search is exact cosine, not HNSW.
