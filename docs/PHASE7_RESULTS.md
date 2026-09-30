# Phase 7 results — evidence-backed research in staging

Measured on `quantpilot-staging` (account `225201316318`, `us-east-1`) on 2026-09-30. Functional staging. Not production. Not a capacity result. Phase 4.8 FakeLLM timings are not combined with these OpenAI results.

## A. Objective

Determine whether the deployed system can turn staged evidence into a research report whose factual claims are cited and supported. The corpus is five SEC company listings ingested through the existing pipeline.

## B. Staging corpus

Real SEC listings only. No synthetic rows.

| Ticker | Legal name | CIK | Exchange | Entity id |
| --- | --- | --- | --- | --- |
| AAPL | Apple Inc. | 0000320193 | NASDAQ | `2c509dc4-bf12-4c8f-ae08-4b96025cfe51` |
| MSFT | MICROSOFT CORP | 0000789019 | NASDAQ | `a6d84c30-9814-454f-99a3-66e1cdeaf237` |
| NVDA | NVIDIA CORP | 0001045810 | NASDAQ | `3462ac4e-98c0-417a-aa56-538b1d91e605` |
| AMD | ADVANCED MICRO DEVICES INC | 0000002488 | NASDAQ | `bd5ca632-e3f6-4fcd-a1ba-b7cfeb55e5ad` |
| META | Meta Platforms, Inc. | 0001326801 | NASDAQ | `6026fafe-7a6a-4e79-aec3-efee09708ff6` |

Ingest run `37f358b4-3a9a-4bab-a9f2-4afcd3cef08d` published 5 Kafka messages. Lake writer landed 5 objects (4484 bytes). Spark job `e8553c34-5eea-47b5-b1d3-f88f01ab5f84` wrote 5 rows. Canonical load attempt `44fe1a5a-1ecf-4409-9e2a-1e168494534c` inserted 5.

News and market were not ingested. Those API keys are unset. Market analytics were not exercised because no valid source data was available.

Procedure: [STAGING_CORPUS.md](./STAGING_CORPUS.md).

## C. Data lineage

Each company row points at its raw S3 object, ingestion `event_id`, `content_hash`, and processing job `e8553c34-5eea-47b5-b1d3-f88f01ab5f84`. The five `event_id` values match the five raw object keys. `POST /api/research/context` for Apple returned the entity with `source.record=companies`, `source.source=sec.company_tickers`, and the raw URI of the Apple object.

Research citations do not copy that S3 URI into `source_url`. The citation locator is `{record: companies, source: sec.company_tickers}` and `source_url` is null. The public SEC file URL is the adapter's fetch URL. It is not stored on the citation.

## D. Entity, event, and relationship validation

Postgres after the load: 5 companies, 5 company entities, 0 events, 0 relationships, 0 news articles, 0 market bars.

No relationship was inferred from the five listings. Co-mention was not turned into a partnership. Ambiguous names were not merged. The relationship research request did not emit a partnership, customer, or supplier claim.

Events were not created. The event pipeline had no news input.

## E. Retrieval validation

`python -m app.retrieval documents` reported inserted 0, updated 0, chunks 0. Embeddings were not created. `FakeEmbeddingProvider` was not used. `EMBEDDING_PROVIDER` stayed the configured OpenAI provider, and there were no chunks to embed.

`GET /api/search?q=Apple+Inc+ticker+CIK&entity_id=2c509dc4-bf12-4c8f-ae08-4b96025cfe51` returned `available: true`, `reason: null`, `results: []`. Empty means no retrieval documents, which the search code treats as an empty result rather than `semantic_unavailable`.

`POST /api/research/context` for the Apple entity returned 1 entity, 0 events, 0 graph facts, 0 market facts, 0 documents, and 1 `structured_fact` citation. The package field `citation_coverage` was `1.0` because that one entity had one citation object. That number is not URL coverage. `source_url` was null.

Date and event filters were not populated. There are no confirmed events and no documents, so those filters have nothing to select.

## F. Neo4j status

`GET /ready` reported `neo4j: unconfigured`. No staging Neo4j endpoint was available. No public Neo4j service was created. Entity, relationship, and event projection were not exercised in AWS staging.

## G. Real LLM research tests

Provider is the staging OpenAI configuration (`gpt-4o-mini`). FakeLLM was not used for these calls.

Three passes, labeled separately:

1. Worker image `phase6-worker`, before the parser fix. 10 requests. 8 `succeeded`, 2 `partial`. 0 claims. Specialists failed `malformed` when the router selected them.
2. Worker image `phase7-research`, parser fix only. Identity questions produced supported listing facts. Announcement questions also produced factual report lines the SEC row does not support. That pass is why the grounding check was added. It is not the final matrix.
3. Worker image `phase7-grounded` (task definition `quantpilot-staging-worker:7`, digest `sha256:b15e5db93f1f18c8023eeefc7b1634d0c3766575fb979fe5dbf3c711481e5e1b`). This is the matrix in section L.

## H. Company-agent failure investigation

Phase 6 job `dee4e38d-5dd2-4843-b3d1-b34c1ac3c177` finished `partial`. The company agent was `gpt-4o-mini`, error `malformed`, about 12 seconds. The evidence package was empty.

A local call with the same provider, prompt, and schema, using the staged Apple package, showed two concrete mismatches:

- The planner user message was the literal string `plan`. `OpenAILLMProvider` dropped `context`, so the model never saw the query. One captured response was a generic plan whose `reasoning_notes` was a list. `MasterPlanPayload` requires a string. The job still finishes `succeeded` and uses `fallback_plan`.
- The company model returned `finding` and `citation_id` instead of `statement` and `citations`, and sometimes a numeric `finding_id`. `extra=forbid` rejected the object. The shared prompt had asked for `citation_id` while the schema field is `citations`.

`LLM_TIMEOUT_SECONDS` is 30. The error type was `malformed`, not `timeout` or `auth`. The provider already retries malformed output up to `LLM_MAX_RETRIES` (3) with sleeps of 1, 2, and 4 seconds. Twelve seconds fits that loop. The raw body was not logged, which is why the field names were recovered by reproducing the call.

Fix that was applied, and no graph redesign:

- The planner user message is the bounded plan context.
- Before validation, alias `finding` to `statement`, `citation_id` to `citations`, stringify `finding_id`, and join a list of `reasoning_notes`. Unknown finding keys are dropped. Statements and citation ids are not invented.
- The shared prompt names `statement` and `citations`. Specialist prompts are `v2`. The planner prompt is `master-v3`.
- A `structured_fact` citation supports a statement only when every content word is in that entity's recorded attributes or a fixed listing vocabulary. Document, event, market, and graph citations are unchanged.

After the parser fix, a local company call on the Apple package returned four facts: legal name Apple Inc., ticker AAPL, CIK 0000320193, exchange NASDAQ, each citing `82c812849e298302`.

## I. Partial-result validation

On the pre-fix image, two jobs were `partial` because specialists failed and the evidence agent still succeeded:

- `765d77b3-2a67-4f2c-af49-74c00a206659` (`phase7-nvda-tech`): company and technology `malformed`. Status `partial`. 0 claims. Limitations included `company unavailable`, `technology unavailable`, and insufficient evidence.
- `55fdb251-5aaa-49f5-b739-150a6c97fe82` (`phase7-aapl-announce`): news, company, and technology `malformed`. Status `partial`. 0 claims. The error shown to the client was `malformed`, not a stack trace.

No unsupported factual claims were stored on those jobs. Local FakeLLM coverage of the same status remains `test_partial_run_persists_partial_status` and `test_llm_timeout_rate_limit_malformed`. Those tests passed. They are not OpenAI scores.

The final grounded matrix had no specialist failure, so it did not create a new `partial` job. Company, news, and technology runs that the router selected finished `succeeded`. Bad statements were rejected by verification instead of failing the agent.

## J. Citation validation

Final matrix only. Every factual report item carried a `structured_fact` citation. Checked by reading the statement against the staged row:

- 22 supported statements restate the cited company's legal name, ticker, CIK, or exchange.
- 0 of those citations have a non-null `source_url`.
- The locator is `companies` / `sec.company_tickers`.
- The citation title matches the company the statement names.
- 7 other claims were `unsupported` and were omitted from report sections. Their text is not in the SEC rows (GPU/AI development, product announcements, including an iPhone 15 claim).

This is not "citation coverage = 100%". URL coverage of report citations is 0 of 22, because the structured citation leaves `source_url` null. Attribute support of the 22 factual lines is 22 of 22.

## K. Evidence validation

The evidence package for a company question contains the entity and one structured citation. It does not contain filings, news, or a business description.

The relationship request (`a748f747-011c-44b4-b386-071276dc374e`) supported two listing facts, one for AMD and one for META. It did not claim a relationship. It also did not say that no relationship was documented.

The technology request stored three unsupported FACT claims about GPUs and artificial intelligence and left the factual section empty.

No conflicting evidence was staged, so conflict preservation was not exercised. Inventing a second source to force a conflict was not done.

## L. 10-request functional matrix

Image `phase7-grounded`. Real OpenAI. One worker. Duplicate submit of the first key returned the same id `31889eeb-3722-4b84-9441-98b1040e5e6b`.

| Key | research_id | Kind | Status | Supported | Unsupported | Factual report lines |
| --- | --- | --- | --- | --- | --- | --- |
| `phase7c-aapl-identity` | `31889eeb-3722-4b84-9441-98b1040e5e6b` | company | succeeded | 4 | 0 | 4 |
| `phase7c-msft-identity` | `d1a2588c-0a68-4a72-8fe3-196e9f34053d` | company | succeeded | 4 | 0 | 4 |
| `phase7c-nvda-identity` | `26c6c72e-bd61-42a3-9f13-3028ebae5e51` | company | succeeded | 4 | 0 | 4 |
| `phase7c-nvda-tech` | `01de170d-b531-4d83-99fd-c160522d83cd` | technology | succeeded | 0 | 3 | 0 |
| `phase7c-amd-meta-rel` | `a748f747-011c-44b4-b386-071276dc374e` | relationship | succeeded | 2 | 0 | 2 |
| `phase7c-meta-identity` | `4f6c7d89-b95c-4130-b24e-3b4d848d234f` | company | succeeded | 4 | 0 | 4 |
| `phase7c-amd-identity` | `5bde59ae-15bd-4af6-833d-d4c7873fce47` | company | succeeded | 4 | 0 | 4 |
| `phase7c-aapl-announce` | `28b95d4d-3c37-44d3-8b30-5333a762c0f8` | event | succeeded | 0 | 2 | 0 |
| `phase7c-ambiguous` | `842c11a9-4fed-4817-b65e-359b29fab5cc` | ambiguous, no entity id | succeeded | 0 | 0 | 0 |
| `phase7c-aapl-product` | `e0caa509-852d-4299-8d1d-591dfa4eb6a0` | insufficient evidence | succeeded | 0 | 2 | 0 |

Market was not requested. No market bars exist. Conflicting evidence was not requested. No second source exists.

The master row's `error` was `malformed` on 8 of these 10 jobs and empty on the NVDA and META identity jobs. Master `status` was `succeeded` in every case. When the planner output is rejected, the existing router selects specialists. Company identity questions include "what is" and "canonical", which that router already selects.

## M. Quality metrics

Staging, real OpenAI, final matrix only. Not comparable to Phase 4.x FakeLLM evaluation scores.

| Metric | Value |
| --- | --- |
| Requests | 10, plus 1 idempotent duplicate |
| Completion (`succeeded`) | 10 of 10 |
| `partial` | 0 of 10 |
| `failed` | 0 of 10 |
| Claims stored | 29 |
| Supported claims | 22 |
| Unsupported claims | 7 |
| Uncertain claims | 0 |
| Conflicting claims | 0 |
| Unsupported claims that appear in factual report sections | 0 |
| Supported statements that match the cited listing attributes | 22 of 22 |
| Report citations with a non-null `source_url` | 0 |
| Specialist runs with status `failed` | 0 |
| Duplicate idempotency key returned the original id | yes, 1 of 1 |

The pre-fix pass is a separate measurement: 2 of 10 `partial`, 0 claims, specialist `malformed` when selected.

## N. Security validation

`TOOL_PERMISSIONS` is unchanged. Specialists still cannot receive arbitrary SQL, Cypher, shell, filesystem, or open HTTP. Ingestion writes company rows. It does not edit that map.

No prompt-injection document was staged. The SEC listing rows do not contain instructions, and there is no document path in this corpus for one to ride in on. A listing row cannot grant a tool the agent was not already allowed to call.

## O. Observability

`GET /ready` after the load: `status=ok`, database revision `0012`, `kafka=ok`, `lake=ok`, `neo4j=unconfigured`, `redis=memory`.

Research requests used correlation ids `phase7c-corr-1` through `phase7c-corr-10`. Worker logs are in `/quantpilot-staging/worker`. The lake writer logged `quantpilot_kafka_consumer_lag` 0 for `quantpilot.company.raw` partition 0 after landing 5 messages.

OpenTelemetry was not enabled. It was left off.

## P. HTTPS status

HTTPS remains pending ACM certificate configuration. The listener is HTTP only. No certificate was created.

## Q. Data integrity

Developer SQLite `backend/data/quantpilot.db` is still `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5` (876544 bytes). `backend/data/lake` does not exist (0 files). TCP `127.0.0.1:7687` was closed.

No production database, bucket, or Kafka cluster was addressed. Staging Neo4j was not configured. Secrets, `.tfstate`, and the raw SEC file were not committed.

## R. Cost and resource status

The Phase 6 stack is still deployed and still billed. One-off ingest, Spark, load, and query tasks have stopped. The registered task definition `quantpilot-staging-spark:1` does not run unless started.

Still running: NAT gateway, public IPv4, ALB, RDS `db.t4g.micro` single-AZ, Kafka `t3.small`, ECS services `api`, `frontend`, and `research-worker` at desired count 1, S3, CloudWatch, and snapshot `quantpilot-staging-phase6`. Dollar amounts were not measured.

The research worker service is task definition `quantpilot-staging-worker:7` (`phase7-grounded`). Terraform state was not refreshed: provider download to `releases.hashicorp.com` timed out, so `terraform apply` was not run. The live worker role policy matches `iam.tf` (`s3:DeleteObject` and `s3:AbortMultipartUpload` on the lake bucket). `environments/staging.tfvars` points `worker_image` at `phase7-grounded` and remains gitignored.

Destroy only this stack, when you choose to:

```bash
terraform -chdir=infra/terraform destroy -var-file=environments/staging.tfvars
```

Snapshot, separately: `aws rds delete-db-snapshot --db-snapshot-identifier quantpilot-staging-phase6`.

## S. Limitations

- Five SEC listing rows. No filings text, no news, no prices, no relationships, no events.
- Factual reports can restate legal name, ticker, CIK, and exchange. They cannot describe what a company does.
- Structured citations have no `source_url`.
- Semantic retrieval and pgvector were not populated.
- Neo4j projection was not run.
- The planner output was rejected on 8 of 10 final requests. The documented router ran instead. Master job status stayed `succeeded`.
- A listing citation used to be enough for the verifier to mark any attached FACT as supported. The intermediate matrix put invented announcements into the factual section. The grounding check stops that for `structured_fact` only.
- The relationship answer stated the two listings and did not state that no relationship exists.
- HTTPS is not configured. RDS is single-AZ. Kafka replication factor is 1.
- No production capacity, HA, zero-downtime, RPO, or RTO claim.

## T. Deferred work

- Configure `NEWS_API_KEY` and `MARKET_API_KEY` before any news, event, or market exercise.
- Put the SEC file URL on the structured citation if a public URL is required. That was not changed here.
- Refresh Terraform state once the AWS provider plugin downloads, so the worker image and lake delete permission are in state as well as in the live account.
- ACM certificate, then HTTPS.
- A staging Neo4j URI, only if it is private.
- Planner output still often fails schema validation after the context fix. The router covers the queries used here. Further planner changes were not made.
