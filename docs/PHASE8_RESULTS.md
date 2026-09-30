# Phase 8 results — evidence corpus and citation URLs

Measured on 2026-09-30. Staging is still `quantpilot-staging` in `us-east-1`. It is not production and not a capacity result.

The news and market corpus was not populated.

`NEWS_API unavailable; news corpus not populated.`

## A. Objective

Populate representative documents, events, relationships, and market bars through the existing pipeline, and make document-derived citations expose the source URL already stored with the source.

The first half did not run. The keys are not configured. The citation change is in the package assembler and is covered by unit tests. It was not rolled onto the staging worker, because staging has no document, event, or graph citation to expose.

## B. Corpus design

The plan is [PHASE8_CORPUS_PLAN.md](./PHASE8_CORPUS_PLAN.md): the five SEC companies already loaded, then 10–30 Massive news articles, events only where the existing pipeline accepts them, relationship candidates only from explicit statements, and daily bars for the existing return windows.

That plan is not executed.

## C. Sources

Registered adapters, unchanged:

| Domain | Adapter | Credential | Present |
| --- | --- | --- | --- |
| company | `SecCompanyAdapter` | `SEC_EDGAR_USER_AGENT` | Set locally (length 40). Not printed. Used in Phase 7. |
| news | `MassiveNewsAdapter` | `NEWS_API_KEY` | Unset locally (length 0). Not a staging secret. |
| market | `MassiveMarketAdapter` | `MARKET_API_KEY` | Unset locally (length 0). Not a staging secret. |

Staging task secrets are `DATABASE_URL`, `OPENAI_API_KEY`, and `JWT_SECRET_KEY` only. `LLM_PROVIDER=openai`. `EMBEDDING_PROVIDER=openai`. `NEO4J_URI` is empty. `OTEL_ENABLED=false`.

## D. Ingestion results

No Phase 8 ingest task was started. No new Kafka messages, lake objects, or Spark jobs.

Phase 7 company path is unchanged: ingest run `37f358b4-3a9a-4bab-a9f2-4afcd3cef08d`, Spark job `e8553c34-5eea-47b5-b1d3-f88f01ab5f84`, load attempt `44fe1a5a-1ecf-4409-9e2a-1e168494534c`, 5 rows inserted.

## E. Data counts

Unchanged from Phase 7. Not re-counted in Postgres this phase, because nothing was written.

| Record | Count |
| --- | --- |
| Companies | 5 |
| Entities | 5 |
| Ingestion records | 5 |
| Raw company objects | 5 |
| News articles | 0 |
| Events | 0 |
| Relationships | 0 |
| Market bars | 0 |
| Retrieval documents | 0 |
| Chunks | 0 |
| Embeddings | 0 |

## F. Lineage validation

Company lineage from Phase 7 still applies: `sec.company_tickers`, S3 `raw_uri`, ingestion event id, content hash, processing job. Those rows have no per-company public URL. The adapter's fetch URL is the bulk SEC file. It is not stored on the company row, and this phase did not copy it onto citations.

News lineage was not created.

## G. Retrieval results

No new documents. Phase 7 search for Apple stayed empty (`available: true`, zero hits) because listings are not retrieval documents. That behavior was not changed.

Unit coverage: a package built from a news source, a confirmed event, and an evidenced relationship returns `document_excerpt`, `event_fact`, and `graph_fact` citations whose `source_url` is the stored `https://example.com/p1`. The company `structured_fact` citation in the same package has `source_url` null.

## H. Embedding results

`python -m app.retrieval embed` was not run. Chunk count is 0. `FakeEmbeddingProvider` was not configured in staging. `EMBEDDING_PROVIDER` remains `openai`.

## I. Event results

No events were created. An article was not turned into a `technology_release` because no article was ingested. The existing rule still requires a confirmed event with a source or evidence id before an event citation is emitted.

## J. Relationship results

No relationship candidates were created in staging. Co-mention was not promoted. The unit package test accepts one `PARTNERED_WITH` edge only when `source_id` and `evidence_id` are present, and that edge's citation now carries the source URL.

## K. Neo4j status

Neo4j remains unconfigured in staging. The task URI is empty. No public Neo4j deployment was created. Projection, reconcile, and repair were not run.

## L. Market analytics status

Market analytics remain unexercised. No bars, no event windows, no benchmark comparison. Nothing was described as causal.

## M. Research test results

No new staging OpenAI matrix. The questions that would need news, events, relationships, or prices do not have evidence in the corpus. Repeating the Phase 7 listing questions would not test documents.

The last real-LLM staging matrix is still Phase 7, image `phase7-grounded`: 10 of 10 `succeeded`, 22 supported listing facts, 7 unsupported claims kept out of the factual section. Those numbers are not Phase 8 retrieval quality.

## N. Claim support results

No new claims. Phase 7 supported-claim count stays 22 of 29 stored claims on that matrix. Unsupported claims in factual sections on that matrix: 0. This phase did not recompute them.

## O. Citation URL coverage

Separated from citation-object coverage.

| Metric | Meaning | Staging documents |
| --- | --- | --- |
| `citation_coverage` | Share of package items that have a citation object. An empty package is 1.0. | Phase 7 Apple package was 1.0 because one listing had one citation object. |
| `citation_url_coverage` | Among document, event, and graph citations, the share whose `source_url` is http(s). Null when none of those kinds are present. A null URL is not covered. Listing and market citations are not in the denominator. | Null for the Phase 7 corpus. There are no document, event, or graph citations. |

Unit example with one valid document URL, one null event URL, and one non-URL graph value: `citation_url_coverage` is `0.3333` (1 of 3). The listing citation in that example is ignored by the denominator.

Phase 7 `source_url` count remains 0 of 22 factual citations. That is URL coverage 0 for those listing citations, and they are not URL-requiring under this definition. Both statements are true. Neither is "citation coverage = 100%".

## P. Citation correctness

No new document claims to score. The unit test checks that the graph, event, and document citations use the source URL that was stored, not a title and not a search URL.

SEC listings remain correct as structured identifiers (name, ticker, CIK, exchange) with `source_url` null.

## Q. Hallucination tests

Not run against new staging documents, because there are none.

Existing rules, unchanged and still tested locally:

- A relationship fact without graph, event, or excerpt support is `unsupported_relationship`.
- Two names in one excerpt do not become a relationship unless the excerpt itself uses a relationship verb and the citation is a document excerpt. The co-mention test in `test_unsupported_cooccurrence_relationship` still expects `UNSUPPORTED`.
- A listing citation does not support an announcement or a product name (`ungrounded_structured_fact`).
- A `technology_release` is not created from a company listing. No event row exists.

## R. Real LLM results

No Phase 8 OpenAI calls. Staging worker image is still `phase7-grounded` (`quantpilot-staging-worker:7`). FakeLLM remains the unit-test provider.

## S. Security results

`TOOL_PERMISSIONS` was not changed. Agents did not gain SQL, Cypher, shell, filesystem, or open HTTP. No injection document was staged.

Lake IAM, read from the live roles, scoped to `quantpilot-staging-lake-225201316318`:

- API: `s3:GetObject`, `s3:ListBucket`, `s3:GetBucketVersioning`.
- Worker: those, plus `s3:PutObject`, `s3:DeleteObject`, `s3:AbortMultipartUpload`.

No new wildcard administrative permission was added. `DeleteObject` stays the Spark commit requirement from Phase 7.

## T. AWS status

The Phase 6 stack is still deployed. Worker task definition is still `:7`. This commit's citation change is not in that image.

Still billing: NAT, public IPv4, ALB, RDS `db.t4g.micro` single-AZ, Kafka `t3.small`, ECS `api`, `frontend`, and `research-worker`, S3, CloudWatch, snapshot `quantpilot-staging-phase6`. One-off tasks are stopped. `quantpilot-staging-spark:1` is registered and not running. Dollar amounts were not measured.

HTTPS remains pending ACM certificate configuration. No certificate was created.

Destroy only this stack, when you choose to:

```bash
terraform -chdir=infra/terraform destroy -var-file=environments/staging.tfvars
```

Snapshot: `aws rds delete-db-snapshot --db-snapshot-identifier quantpilot-staging-phase6`.

## U. Data integrity

Developer SQLite `backend/data/quantpilot.db` is `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5` (876544 bytes). `backend/data/lake` does not exist. TCP `127.0.0.1:7687` was closed.

No production database, bucket, or Kafka cluster was addressed. No secret, `.tfstate`, article dump, or database dump is in the change.

## V. Limitations

- News, events, relationships, market bars, retrieval documents, chunks, and embeddings are still zero.
- Technology, partnership, acquisition, and market-reaction questions still have no staged evidence.
- Listing citations still have no `source_url`.
- `citation_url_coverage` is null on the current staging corpus because there is no URL-requiring citation.
- The URL pass-through is tested locally. The running staging worker does not have this commit.
- Neo4j is unconfigured. HTTPS is HTTP only. OpenTelemetry is off.
- No production capacity, HA, zero-downtime, or causal-impact claim.

## W. Deferred work

- Set `NEWS_API_KEY` and `MARKET_API_KEY` on the staging task, then run the commands in the corpus plan.
- Roll a worker image that includes the citation URL pass-through after those documents exist.
- A private Neo4j URI, if one is actually available.
- ACM, then HTTPS.
