# Phase 9 plan — small Massive corpus on staging

Baseline: `5e1d9b88339ba0ea9cce9ec402f1b88ba7a9add9` (`fix: harden evidence and citation lineage`).

The goal is a small real news and daily-bar corpus for AAPL, MSFT, NVDA, AMD, META, and SPY bars, through the existing path. No second provider, no yfinance ingest, no fabricated articles, prices, relationships, or events.

## Audit

The Phase 2.5 Massive path is already the ingestion path. Nothing behind the publisher is rebuilt.

| Piece | Where | Phase 9 use |
| --- | --- | --- |
| News client | `MassiveNewsAdapter`, `GET /v2/reference/news` | Ticker filter, `published_utc` window, `next_url` pagination, Bearer auth |
| Market client | `MassiveMarketAdapter`, daily aggregates, `adjusted=false` | One stream per symbol, including SPY |
| HTTP | `SourceClient` | Throttle (default 5/min), timeout 20s, bounded retry, Retry-After. The key stays in the Authorization header and is not logged |
| Identity | `NewsRecord` / `MarketRecord` | Provider `id`, or `derived:` plus URL, publisher, and time. Content hash and `event_id` come from the existing contract |
| Runner | `IngestionRunner` | Run row, in-run dedup, Kafka publish, invalid rows to the DLQ |
| Lake / Spark / load | existing `lake-writer`, `process`, `load` | Same commands as the SEC listing run |
| Documents | `python -m app.retrieval documents` then `embed` | News title and summary. Staging `ENVIRONMENT=production` refuses `EMBEDDING_PROVIDER=fake` |
| Events | `propose_news_candidates` | A ticker-tagged article becomes a pending `NEWS` candidate. An article with no tickers stays a document |
| Impact | `python -m app.market impact` | Confirmed events only. Windows `-1`, `+1`, `+3`, `+5`, `+20` sessions. Benchmark `SPY`. Close, not adjusted close |
| Citations | Phase 8 `http_source_url` and `citation_url_coverage` | On the worker that runs research. Listing citations stay null |

The first staging market run received 264 daily bars and published none. Massive `v` is a fractional float (a sampled AAPL bar was not a whole number). The existing contract and Spark rule required a non-negative whole number, so every bar was dead-lettered. Phase 9 stores that provider value as `DECIMAL(20,6)` on `market_bars.volume` (migration `0013`). It does not round the value to a whole share. Negative and non-numeric volumes stay invalid.

Missing before this phase: there was no command that confirms a pending news candidate. `EventCandidateService.accept` existed and was only called from tests. Phase 9 adds `accept_resolved_news_candidates`: a pending `NEWS` candidate is confirmed only when at least one provider ticker resolves to an existing entity. The event type stays `NEWS`. Unresolved candidates stay pending. No partnership, acquisition, or technology-use type is inferred.

Relationships are not extracted in this phase. Co-mention is not a relationship. The accepted-relationship count stays zero unless an existing explicit-evidence command produces one. It does not.

Neo4j stays unconfigured. Impact rows stay in Postgres.

## Staging configuration

The Massive key is already in Secrets Manager as `quantpilot-staging/news-api-key` and `quantpilot-staging/market-api-key`, injected as `NEWS_API_KEY` and `MARKET_API_KEY` on the API and worker task definitions. The frontend task does not receive them. `/ready` is not changed.

The running worker image before this phase is `phase7-grounded`, which does not include the citation URL change or the accept command. Ingest, retrieval, events, and research one-offs use a new app image built with `REQUIREMENTS=requirements-pipeline.txt`. Spark stays the existing one-off image and task definition. The 1 GB research worker is not pointed at the Spark image.

Terraform source already names the two secrets. State was not refreshed when they were created. This phase does not run `terraform apply`.

## Bounded window

News publication window: `2026-09-15` through `2026-10-03` UTC, symbols `AAPL,MSFT,NVDA,AMD,META`, `--max-records 50`, `--page-size 10`.

Market trading window: `2026-08-03` through `2026-10-03`, symbols those five plus `SPY`, so a confirmed event in the news window can see about twenty prior sessions and whatever later sessions exist. `--max-records` stays at the default 1000, which is above six symbols of daily bars in that window. No intraday request.

Raw objects land on the UTC day the ingest runs. Spark `--start` / `--end` use that ingestion date, not the article date.

## Research

After documents, embeddings, confirmed events, and impact rows exist, about twelve `POST /api/research` jobs use real `gpt-4o-mini`. Each request passes the staged `entity_ids` the question is about. Queries follow the corpus that actually loaded. A question may return insufficient evidence. Citation object coverage and citation URL coverage are recorded separately. A null URL stays null.

## Out of scope

No new frontend. No schema migration unless a real gap appears. No production-capacity claim. No causal wording. No buy, hold, or sell. No commit of secrets, tfstate, dumps, or `.env`.
