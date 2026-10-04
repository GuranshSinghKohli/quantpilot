# Phase 9 results — bounded Massive corpus on staging

Staging only. This is not a production deployment, a capacity result, or a causal market study.

## Facts

Provider: Massive (formerly Polygon.io), existing `MassiveNewsAdapter` and `MassiveMarketAdapter`. News `GET /v2/reference/news`. Daily bars `GET /v2/aggs/ticker/{symbol}/range/1/day/{from}/{to}` with `adjusted=false`. Auth is `Authorization: Bearer` from `NEWS_API_KEY` and `MARKET_API_KEY`. The key is not in this document.

Environment: AWS account `225201316318`, `us-east-1`, cluster `quantpilot-staging`, bucket `quantpilot-staging-lake-225201316318`, RDS `quantpilot-staging`. Alembic head on that database is `0013`.

Images:

| Role | Tag | Digest | Task definition |
| --- | --- | --- | --- |
| Research worker and one-off ingest/load | `phase9-worker` | `sha256:aff5d8fc303c1b4c40d5504a936f39309bc923482b433b880612c4935d6f0d8d` | `quantpilot-staging-worker:10` |
| Spark one-off | `phase9-spark` | `sha256:a86527c905aeb02a4aee54d8a2599ef9b895e91d8ecfe216818b12d6e5d1ecb5` | `quantpilot-staging-spark:2` |

The standing research-worker service is on `:10`, rollout completed, desired 1. The API service stayed on `quantpilot-staging-api:5`. It submits and reads jobs. The graph runs in the worker. Both API and worker task definitions inject `NEWS_API_KEY` and `MARKET_API_KEY`. The frontend task does not.

### Corpus

| Item | Count |
| --- | --- |
| News articles | 72 |
| Raw news objects | 72 |
| Market bars | 264 (44 each: AAPL, MSFT, NVDA, AMD, META, SPY) |
| Raw market objects | 264 |
| Retrieval documents | 72 |
| Chunks | 72 |
| Embeddings | 72, model `text-embedding-3-small`, dimension 1536 |
| Event candidates | 72, all `NEWS`, all accepted |
| Confirmed events | 72 |
| Event impact rows | 144, calculation version `v1`, failed 0, unavailable 0 |
| Relationships | 0 |
| Relationship candidates | 0 |
| News source URLs | 72 `https`, 0 `http`, 0 null |
| Ingestion records by status | `loaded` 341 (5 prior SEC listings + 72 news + 264 bars) |

Ticker tags on articles, an article can carry more than one: AAPL 50, NVDA 37, MSFT 34, META 14, AMD 9. The first news run's global cap filled with AAPL (50). A second run added MSFT 5, NVDA 5, AMD 6, META 6.

News window: published `2026-09-15` through `2026-10-03` UTC. Bar window: trading dates `2026-08-03` through `2026-10-03`. Daily bars only. No intraday request.

### Pipeline

| Step | Result |
| --- | --- |
| News ingest `ea0045e012514234b941ff4d404da5ea` | COMPLETED, published 50, all AAPL, `quantpilot.news.raw` |
| News ingest `5d71f226290c442e801cafcd3026e010` | COMPLETED for MSFT 5, NVDA 5, AMD 6, META 6 |
| Market ingest `6611f68a97094f68b69e1616618c69a2` run `167d6724-ca2d-45d6-87d6-b232f17fa29a` | FAILED before the decimal-volume change. Received 264, valid 0, dead-lettered 264. Reason: volume was not a whole number |
| Migration `0013` and market ingest `e35ac081eef2482ab9cda99e877356c0` run `12b4f4c7-9020-4c1b-9df2-5f7998ba` | COMPLETED, published 264, invalid 0, six HTTP 200s |
| Lake writer `d87e1245f00f45c38fc948706f794ca2` | Exit 0. Raw prefixes `raw/news/year=2026/month=10/day=03/` (72) and `raw/market/...` (264) |
| Spark `c657cd32bd384b8db6adef4fdaa8a2b7` | News job `5bcf02fa-82bc-4fa4-bbba-eb4c82bd5011` output 72. Market job `757f96a4-ac69-4214-9c9b-fda411c91255` input 264, output 264, rejected 0 |
| Load | News inserted 72, attempt `20f3a4f4-1052-4e69-83ee-61f4b3e47a66`. Market inserted 264, attempt `2cb25c7e-7976-487a-a653-8256cf83bcee`. Both `CANONICAL_LOAD_COMPLETE` |
| Reconcile task `7a0a892bdaae4202a56b4482a49ca1fe` | `ok: true`, records 341, jobs 3, problems `[]` |
| Documents, embed, events, impact task `6a5f4bc497d946ecbe48a6332c8ac0a1` | Exit 0. Embed provider `openai`. Accept report: examined 72, accepted 72, left pending 0, failed 0 |

Embedding provider was the staging `EMBEDDING_PROVIDER=openai`. `ENVIRONMENT=production` refuses `fake`. No fake embeddings were written.

Events stayed type `NEWS`. Acceptance used `accept_resolved_news_candidates`: a pending candidate is confirmed only when a provider ticker resolves to an existing entity. No partnership, acquisition, or technology-use type was assigned. Unresolved tickers are not given an entity row.

### Research

Twelve `POST /api/research` jobs, real `gpt-4o-mini`, worker `:10`. All 12 status `succeeded`. Zero `partial`, zero `failed`.

| | |
| --- | --- |
| Claims | 110 |
| Supported | 92 |
| Uncertain | 16 |
| Unsupported | 2 |
| Conflicting | 0 |

Report citations that are document or event facts and carry an `https` URL: 48. Report citations with a null URL: 2, both `market_fact` on job `phase9-07` (`2f15726e-00b0-49c8-909d-361cb6a56bb4`). Market-window citations are outside the URL denominator. Listing citations were not the URL-bearing kind in these reports. The GET payload does not include the package field `citation_url_coverage`; the counts above are from the stored report citations.

Jobs that returned no claims and the limitation `insufficient evidence for a verified factual section`:

- `phase9-04` `e124f706-02ec-493d-9b07-68b7427bc0b2` (selected NVDA-linked event)
- `phase9-06` `e10971ee-af75-4550-a8ca-3e71aeada5bb` (NVDA–MSFT relationship)
- `phase9-09` `20afe096-ec23-4dd1-8f64-0fb58b97b49e` (Meta)

Unsupported statements, excluded from the factual section on the job that recorded that limitation:

- `phase9-02`: "Microsoft's stock is rated as a Hold compared to Oracle's Buy rating."
- `phase9-11`: "Advanced Micro Devices Inc is listed on NASDAQ with the ticker symbol AMD and has a CIK of 0000002488."

The master planner returned `error: malformed` on 12 of 12 jobs. Job status stayed `succeeded`. The keyword router selected the specialist that ran. News, company, technology, and market agents succeeded when selected and were skipped otherwise. Evidence succeeded on every job.

One supported factual line on `phase9-10` (`55909e3b-85e0-43ea-a480-01224f7671b1`) restates an article sentence that AMD has partnerships with OpenAI, Meta, and Anthropic, cited to `https://www.zacks.com/stock/news/2989998/amd-s-recent-surge-still-matters-putting-these-etfs-in-focus`. The relationship table was not written. Co-mention was not turned into a relationship row.

`phase9-07` market agent succeeded. Its report cites an event URL and two `market_fact` citations with `source_url` null. That null is the calculated window, not a missing article URL.

### Code that changed for this phase

The adapters, throttle (5/minute), 20s timeout, retry, cursor pagination, and runner were already in place. Two gaps were closed:

- `python -m app.market accept-resolved-news` confirms pending `NEWS` candidates with a resolved ticker. It does not invent an event type.
- `market_bars.volume` is `NUMERIC(20,6)` (migration `0013`). Spark stores the same decimal. Massive `v` on the sampled bars was a fractional float, so the previous whole-number rule dead-lettered every bar. The stored value is the provider figure at six decimal places. It is not rounded to a whole share.

## Observations

The global `--max-records` cap is consumed by the first ticker stream. A single ingest of five symbols with `--max-records 50` published 50 AAPL articles and zero for the other four. Separate bounded runs were required.

Articles are headline and description text from Massive, not full bodies. Each became one chunk. Several NVDA-tagged articles are ETF commentary that carries an NVDA ticker, not an NVIDIA product announcement.

Impact rows are one per confirmed event-entity link. 144 rows for 72 events means some articles resolved more than one staged ticker. Windows are session offsets on unadjusted close versus SPY. Missing future sessions stay null inside the row. The batch report's `unavailable` count was 0, which means each calculated link produced a row, not that every +20 session existed.

The planner still fails schema validation on this model and falls back to the keyword router. That matches the Phase 7 staging note. It was not changed here.

## Limitations

- Not production capacity, HA, zero-downtime, or a reliability claim. RDS is single-AZ. Kafka replication factor is 1. The ALB is HTTP. Neo4j is unconfigured. OpenTelemetry is off.
- No buy, hold, or sell product. One unsupported claim was a Hold/Buy rating sentence, and it was excluded.
- No causal claim. Impact fields stay return, relative return, abnormal-return proxy, volume ratio, and volatility.
- Relationship edges were not extracted. A cited article sentence about partnerships is not a `relationships` row.
- The first market run's 264 invalid bars were dead-lettered. They were not repaired in place. The successful run re-fetched them after `0013`.
- Terraform source names the two Massive secrets. State was not imported. `terraform apply` was not run.
- Spark unit tests were not executed in this workspace. The staging Spark job is the check that ran: 264 market rows in, 264 out, 0 rejected.
- Developer SQLite `backend/data/quantpilot.db` sha256 remained `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5`.

## Commands

Staging one-offs used task definition `quantpilot-staging-worker:10` except Spark, which used `quantpilot-staging-spark:2`. Subnet `subnet-08fbfe9f4e27889c8`, worker security group `sg-0b8f7f4626235d51a`, `assignPublicIp` disabled.

```text
python -m app.pipeline ingest news --symbols AAPL,MSFT,NVDA,AMD,META --start 2026-09-15 --end 2026-10-03 --max-records 50 --page-size 10
python -m app.pipeline ingest news --symbols MSFT --start 2026-09-15 --end 2026-10-03 --max-records 8 --page-size 8
# same flags for NVDA, AMD, META
alembic upgrade head
python -m app.pipeline ingest market --symbols AAPL,MSFT,NVDA,AMD,META,SPY --start 2026-08-03 --end 2026-10-03 --max-records 1000 --page-size 100
python -m app.pipeline lake-writer --max-messages 400 --idle-timeout 60
python -m app.pipeline process news --start 2026-10-03 --end 2026-10-03
python -m app.pipeline process market --start 2026-10-03 --end 2026-10-03
python -m app.pipeline load --domain news --latest
python -m app.pipeline load --domain market --latest
python -m app.pipeline reconcile
python -m app.retrieval documents
python -m app.retrieval embed
python -m app.market events-from-news
python -m app.market accept-resolved-news
python -m app.market impact
```

Local tests after the volume and accept changes: `tests/unit/pipeline/test_contracts.py`, `tests/unit/pipeline/test_source_adapters.py`, `tests/unit/market/test_event_candidates.py`, `tests/unit/market/test_calculator.py`. Final run: 113 passed. An earlier combined run also included `tests/unit/pipeline/test_canonical_load.py` and failed one calculator case (`float` divided by `Decimal` in the impact ratio). That case passed after the ratio used float volumes. `python -m app.pipeline schemas --check` matched the regenerated JSON.

Configuration required, and already present on the staging tasks: `NEWS_API_KEY`, `MARKET_API_KEY`, `OPENAI_API_KEY`, `DATABASE_URL`, `SEC_EDGAR_USER_AGENT` was already used for the five listings. No new secret was committed.
