# Phase 7 plan — staging corpus and evidence-backed research

Phase 6 deployed `quantpilot-staging` and showed the research path runs. The corpus was empty, so finished jobs had no claims and no citations. Phase 7 checks whether that same deployment can produce a citation-backed report from staged evidence.

This is not a performance test, a production deploy, or a new product surface.

## Constraints

- Use the existing adapter, `IngestionRunner`, Kafka, lake writer, Spark, and canonical loader.
- Do not insert rows into research tables to fake a corpus.
- Do not add agents, browsing, recommendations, or buy/sell/hold behavior.
- Do not weaken verification.
- Synthetic fixtures only where a real adapter cannot run, and only if marked synthetic.
- Staging writes stay on `quantpilot-staging` in account `225201316318`, `us-east-1`.
- FakeLLM stays the local test provider. The staging research calls use the configured OpenAI model.
- Do not create a public Neo4j endpoint. Do not invent an ACM certificate.
- Leave the staging stack up unless the operator asks to destroy it.

## Corpus

The only configured source is the SEC company adapter (`SEC_EDGAR_USER_AGENT` is set). `NEWS_API_KEY` and `MARKET_API_KEY` are not set, so news and market are not ingested.

Load five real SEC listings: AAPL, MSFT, NVDA, AMD, META. That is enough to exercise company identity. It is not a news, technology, relationship, or market corpus. Questions those listings cannot answer must end as a limitation, not a generated fact.

## Path

1. `python -m app.pipeline ingest company` inside the VPC, publishing `quantpilot.company.raw`.
2. One-off lake writer, max 5 messages.
3. One-off Spark `process company` on the `spark` image. The standing research worker is 1 GB and is not retargeted at Spark.
4. `python -m app.pipeline load --domain company --latest` on the worker image.
5. `python -m app.retrieval documents`. Embed only if chunks exist.
6. Real OpenAI research requests that name resolved entity ids when the question is about a staged company.
7. Inspect claims, citations, verification, and limitations. Count only what the responses contain.

## Known gaps to record, not paper over

- Company listings do not become retrieval documents. Semantic search can be empty.
- Structured citations carry a locator, not necessarily a public URL.
- Neo4j is unconfigured.
- HTTPS stays pending an ACM certificate.
- Market analytics are not exercised when no market source is configured.
