# Evidence architecture (Phase 4.1)

SOURCE, DOCUMENT, EVIDENCE, CHUNK, EMBEDDING, RETRIEVAL RESULT, and EVIDENCE PACKAGE are different objects.

- **Source** (`sources`): origin URL, publisher, `content_hash`, optional `raw_uri` into the lake.
- **Document** (`retrieval_documents`): normalized retrieval projection of a source. Identity is `source_key|external_id` or `url_hash`, never title.
- **Evidence** (`evidence_records`): immutable excerpt keyed by `(source_id, content_hash)`. Optional `document_id` and `evidence_type`.
- **Chunk** (`retrieval_chunks`): deterministic retrieval unit of one document version.
- **Embedding** (`chunk_embeddings`): vector for `(chunk_id, model_name, model_version)`.
- **Retrieval result**: ranked hit with provenance; not stored.
- **Evidence Package**: bounded JSON context for a future agent. Not an agent output.

Document text used for retrieval is title + summary (what Postgres stores). Raw lake objects remain the durable payload. pgvector is not the content store.

Historical evidence is not rewritten when document text changes. A new `content_version` gets new chunks; old chunks stay keyed by the previous `document_content_hash`.
