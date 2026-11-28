# Phase 8 plan — evidence corpus and citation URLs

Phase 7 can restate five SEC listings. It cannot show news, events, relationships, market windows, or a navigable source URL. Phase 8 was supposed to fill that corpus through the adapters that already exist.

## Stop rule

If `NEWS_API_KEY` is unset, do not invent articles. Record `NEWS_API unavailable; news corpus not populated.` If `MARKET_API_KEY` is unset, leave market analytics unexercised. Do not add an adapter, a second retrieval index, or a public Neo4j.

## What can still change

Document, event, and graph citations must copy the http(s) URL already stored on `sources.url`. Listing citations stay without a URL. Report `citation_coverage` and `citation_url_coverage` as different numbers. A null URL is not covered.

Keep the Phase 7 parser aliases (`finding` → `statement`, `citation_id` → `citations`) and the listing grounding check.

## Out of scope

New agents, Kafka, Postgres, pgvector, S3, Spark, recommendations, browsing, MCP, and a load-test rerun.
