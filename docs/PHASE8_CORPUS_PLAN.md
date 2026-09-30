# Phase 8 corpus plan

The intended corpus is small and traceable. It was not loaded. `NEWS_API_KEY` and `MARKET_API_KEY` are unset locally and are not present on the staging API or worker tasks.

`NEWS_API unavailable; news corpus not populated.`

Market analytics remain unexercised. No prices, articles, partnerships, or events were invented.

## Adapters that already exist

| Domain | Provider | Setting | Staging |
| --- | --- | --- | --- |
| company | SEC `company_tickers_exchange.json` | `SEC_EDGAR_USER_AGENT` | Loaded in Phase 7. Five listings. Not retrieval documents. |
| news | Massive `GET /v2/reference/news` | `NEWS_API_KEY` | Not set. |
| market | Massive daily aggregates | `MARKET_API_KEY` | Not set. |

There is no second news or filings adapter. Adding one would be a new ingestion path. This phase does not add one.

## Intended shape, once a key exists

Use the existing runner only: adapter, Kafka, lake writer, Spark, canonical load, then `python -m app.retrieval documents` and `embed`.

- Companies: the five already staged (AAPL, MSFT, NVDA, AMD, META). Do not add tickers until a document mentions them.
- News: 10–30 Massive articles whose body discusses a launch, a partnership, an acquisition, or a product event for those tickers. A name in a headline is not enough.
- Events: only articles the existing event pipeline accepts as `news` or `technology_release`. Other articles stay documents.
- Relationships: candidates only from an explicit statement in a source. Co-mention stays unresolved. Accept only when the existing evidence rule passes.
- Market: daily bars covering the event dates plus the existing windows (`-1`, `+1`, `+3`, `+5`, `+20` sessions) and `MARKET_BENCHMARK_SYMBOL`. No bar is filled in by hand.

Each news row already carries `article_url`, publisher, `published_utc`, and a content hash. That URL is the citation URL. Do not replace it with a search URL.

## What stays without a URL

SEC listing rows store an S3 `raw_uri` and `source=sec.company_tickers`. They do not store a per-company public URL. Their citations stay `structured_fact` with `source_url` null. That is not a missing document URL.

## Rebuild

Do not run ingest until the key is configured on the staging task, not only on a laptop. Confirm cluster `quantpilot-staging`, bucket `quantpilot-staging-lake-225201316318`, and database host `quantpilot-staging.cc1w0uiuww3e.us-east-1.rds.amazonaws.com` before any command. Do not print the key or `DATABASE_URL`.

Commands, when a key exists:

```bash
python -m app.pipeline ingest news --symbols AAPL,MSFT,NVDA,AMD,META
python -m app.pipeline ingest market --symbols AAPL,MSFT,NVDA,AMD,META
python -m app.pipeline lake-writer --max-messages <n> --idle-timeout 45
python -m app.pipeline process news
python -m app.pipeline process market
python -m app.pipeline load --domain news --latest
python -m app.pipeline load --domain market --latest
python -m app.retrieval documents
python -m app.retrieval embed
```

Spark stays a one-off task with enough memory. Do not point the 1 GB research worker at the Spark image. Event and graph commands stay the existing market and graph CLIs. They were not run in this phase.
