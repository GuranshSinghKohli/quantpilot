# Staging corpus

The corpus lives only in `quantpilot-staging` (account `225201316318`, `us-east-1`). It is not the developer SQLite database, the local lake, or a production system. There is no production QuantPilot deployment.

## What is loaded

Five company listings from the SEC file `https://www.sec.gov/files/company_tickers_exchange.json`, filtered by the existing SEC adapter. Nothing in this set is synthetic.

| Ticker | CIK | Legal name | Exchange stored | Entity id |
| --- | --- | --- | --- | --- |
| AAPL | 0000320193 | Apple Inc. | NASDAQ | `2c509dc4-bf12-4c8f-ae08-4b96025cfe51` |
| MSFT | 0000789019 | MICROSOFT CORP | NASDAQ | `a6d84c30-9814-454f-99a3-66e1cdeaf237` |
| NVDA | 0001045810 | NVIDIA CORP | NASDAQ | `3462ac4e-98c0-417a-aa56-538b1d91e605` |
| AMD | 0000002488 | ADVANCED MICRO DEVICES INC | NASDAQ | `bd5ca632-e3f6-4fcd-a1ba-b7cfeb55e5ad` |
| META | 0001326801 | Meta Platforms, Inc. | NASDAQ | `6026fafe-7a6a-4e79-aec3-efee09708ff6` |

The SEC payload said `Nasdaq`. The canonical loader stored `NASDAQ`. Each row's source is `sec.company_tickers`.

Not loaded. Phase 8 rechecked on 2026-09-30: `NEWS_API_KEY` and `MARKET_API_KEY` are unset in the developer environment and are not secrets on the staging API or worker tasks. Neo4j URI on the staging task is empty.

- News articles and events. `NEWS_API unavailable; news corpus not populated.`
- Market bars. Market analytics remain unexercised.
- Technology entities, relationship edges, and retrieval documents.

No news ingest, Spark news job, event build, or embedding job was run in Phase 8. Inventing articles or prices was not a substitute.

No prices, articles, partnerships, or technology descriptions were invented.

## How it was ingested

All of these ran as one-off ECS tasks in the staging VPC (private subnet `subnet-08fbfe9f4e27889c8`, worker security group `sg-0b8f7f4626235d51a`). They were not pointed at the developer database.

1. Ingest, worker image, command `python -m app.pipeline ingest company --symbols AAPL,MSFT,NVDA,AMD,META --max-records 10`. Task `a3d4cf5c90f54ccdbc59c2af0554c587` exited 0. Run `37f358b4-3a9a-4bab-a9f2-4afcd3cef08d`. Five messages on `quantpilot.company.raw`.
2. Lake writer, `python -m app.pipeline lake-writer --max-messages 5 --idle-timeout 45`. Task `2dad13d56e8d4bbb8fa88b3de3622e8f` exited 0. Five objects under `s3://quantpilot-staging-lake-225201316318/raw/company/year=2026/month=09/day=30/`. 4484 bytes. Consumer lag on that partition was 0.
3. Spark, image `quantpilot-staging-worker:phase7-spark`, task definition `quantpilot-staging-spark:1` (1024 CPU, 4096 MB, `SPARK_DRIVER_MEMORY=1536m`, `SPARK_MASTER=local[1]`). Command `python -m app.pipeline process company --start 2026-09-30 --end 2026-09-30`. The first attempt failed because the worker role could not `s3:DeleteObject` during the S3A commit. After that action was added on the lake bucket only, task `4aeb37b2ac474cf49aef8a977e15c9f8` exited 0. Job `e8553c34-5eea-47b5-b1d3-f88f01ab5f84` completed with 5 input objects and 5 output rows. Parquet: `processed/company/year=2026/month=09/day=30/part-00000-c7a4bb34-4960-4732-b268-88b99ff114af.c000.snappy.parquet` (10520 bytes).
4. Canonical load, worker image, `python -m app.pipeline load --domain company --latest`. Task `8a3d8067bd1641978651588e4e9fd2d5` exited 0. Attempt `44fe1a5a-1ecf-4409-9e2a-1e168494534c` inserted 5, updated 0, failed 0.
5. Retrieval documents, `python -m app.retrieval documents`. Inserted 0, chunks 0. Embed was not run. There were no chunks, and `FakeEmbeddingProvider` was not substituted.

The Spark task definition is a one-off. It is not the research-worker service. The standing worker stays on the app image (`phase7-grounded` after the research fixes).

## Lineage

For each company, Postgres stores `source`, `raw_uri`, `event_id`, `content_hash`, and `processing_job_id`. The `event_id` matches the raw object key. Example for Apple Inc.:

- raw object `.../8bd79de33052fc2c25e55dfb1a5b1c2f184c5afb24df9425718b503ba4d627b2.json`
- ingestion event id `8bd79de33052fc2c25e55dfb1a5b1c2f184c5afb24df9425718b503ba4d627b2`
- processing job `e8553c34-5eea-47b5-b1d3-f88f01ab5f84`
- entity `2c509dc4-bf12-4c8f-ae08-4b96025cfe51`

`content_hash` is the canonical row hash. It is not the SHA-256 of the raw envelope. Both are kept.

Counts after the load, before research: companies 5, entities 5, ingestion records 5, events 0, relationships 0, news articles 0, market bars 0, retrieval documents 0, chunks 0, embeddings 0.

## Rebuild

Repeat the five commands above against `quantpilot-staging` only. The company loader upserts by CIK. This phase measured one insert of five rows. It did not run a second ingest to time a replay.

`SEC_EDGAR_USER_AGENT` must be a real contact string. Pass it as a container override. Do not commit it.

Do not run `make ingest-company` or the compose profiles against staging. Those commands use the local Kafka and MinIO settings.

## Reset and isolation

There is no separate "wipe staging tables" command. Destroying the stack deletes the database and the lake:

```bash
terraform -chdir=infra/terraform destroy -var-file=environments/staging.tfvars
```

That file is gitignored and applies only to this state. Do not point it at another VPC.

Before any ingest, confirm the task is in cluster `quantpilot-staging`, the bucket is `quantpilot-staging-lake-225201316318`, and the database host is `quantpilot-staging.cc1w0uiuww3e.us-east-1.rds.amazonaws.com`. Do not print `DATABASE_URL`.
