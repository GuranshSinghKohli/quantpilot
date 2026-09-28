# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A reviewer of this repo — hiring manager, course staff, or the builder walking the graph. Copy and chrome should assume the reader wants to see the pipeline, the stack, and the evidence. Not a retail brokerage audience and not an automated investment advisor.

## Product Purpose

QuantPilot detects important corporate and technology events, maps relationships between companies, technologies, investments, partnerships and competitors, analyzes related market movements, and uses specialized AI agents to produce source-backed research and second-order impact analysis.

It is a research/intelligence system. It is not a stock-price predictor or an automated investment recommendation engine. Facts, observations, inferences and scenarios stay separate.

## Positioning

Core workflow: Event → Entity → Relationship → Market → Research → Verification → Report.

Important claims must be traceable to evidence. Conflicting evidence is surfaced rather than silently resolved. Market windows around events are Event Impact Analysis — a coincident move is not causation.

## Operating Context

Next.js / TypeScript frontend talking to FastAPI. Current working path still uses LangGraph research, the Evidence Ledger, company memory, yfinance, and SEC EDGAR. Phase 2 has shipped a local data path beside it (compose profiles, not yet feeding research): source adapters → single-node Kafka → MinIO raw lake → PySpark `local[*]` → processed Parquet → manifest-driven canonical loader → Postgres (`companies`, `market_bars`, `news_articles`), with `/metrics` and CLI status/reconcile/recovery. Phase 3.1 adds entity aliases, conservative resolution, evidenced relationships, and an optional Neo4j projection of those Postgres tables. Phase 3.2 adds isolated Neo4j tests, read-only reconciliation, opt-in repair, deterministic company enrichment, and a relationship-candidate pipeline that is not a graph edge until accepted. Phase 3.3 adds a deterministic market/event intelligence layer: event candidates, confirmed events, trading-session windows on canonical `market_bars`, and `event_impacts` (simple benchmark-adjusted returns, not forecasts). Accepted event/entity associations project to Neo4j as `:CanonicalEvent-[:INVOLVES]->:Entity`. Target architecture (build incrementally) adds research over the graph, managed Kafka, AWS S3, a Spark cluster, Airflow, pgvector, Terraform, and AWS.

Removed from this product: personal portfolio / holdings, broker sync, daily briefings, investment ideas, investment memos, and price-alert trading rules.

## Capabilities and Constraints

- Do not fabricate data, sources, performance metrics or evaluation results.
- Do not issue buy / hold / sell recommendations as a product feature.
- Every important claim should retain source metadata and uncertainty.
- Auth exists so research history can persist; it is not a brokerage login.

## Brand Commitments

Product name is "QuantPilot." Visual world remains the user-pinned Cyberpunk / Glitch desk unless a later design pass changes it.

## Product Principles

1. Every important claim traces to evidence.
2. Show the pipeline's work, including verification and contradiction.
3. Separate fact, observation, inference, and scenario.
4. No investment recommendations and no personal trading-desk features.
5. Build incrementally against the Master Build Specification. Do not generate the entire target platform at once.
6. This is a project that must explain itself: name the workflow and the tech stack on the desk and in the repo. UI stays a technical bench, not a consumer app. When a layer ships, update the on-desk stack list — never list Kafka / Neo4j / Spark as current until the code uses them.
