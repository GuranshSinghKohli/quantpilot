# Phase 10 results — planner schema and research desk

Code commit: `c830fe5fe3af0953f47eafaae5aab1ce02da214d`.

Local measurements and the staging matrix are separate. The local 24/24 planner result is not a staging result. Phase 9's deployed 12/12 malformed figure is the baseline this staging run is compared with.

## Local implementation

### Planner root cause

Phase 9 recorded master `error: malformed` on 12 of 12 jobs. The stored row does not keep the model body.

A local reproduction used the Phase 9 call: `gpt-4o-mini`, `response_format: {"type": "json_object"}`, prompt `master-v3`, schema `MasterPlanPayload`. Fifteen substantive or empty queries:

- 12 returned `evidence_requirements` as a prose string. Validation failed with `evidence_requirements: list_type`.
- 3 of those 12 also returned `requested_sections` as a string.
- `selected_agents` was usually a list of allowed names.
- 3 empty or underspecified queries returned empty lists and validated.

`json_object` requires JSON. It does not require arrays. The provider retried the same messages. The coerce step only joined a list of `reasoning_notes`. A string was not rewritten into a list. `accept_plan` never saw those 12 objects, so the keyword router selected the specialists. Job status stayed `succeeded`.

### Fix

- Planner requests now use strict `json_schema` (`master_response_format`): arrays, agent and section enums, `additionalProperties: false`.
- If the API rejects that format, the same attempt is sent once as `json_object`.
- The graph allows one corrective call when the first result is `malformed` or `invalid_plan`. The second user message includes the validation hint. There is no third planner call.
- Prompt version is `master-v4`.
- Unknown specialists still fail `accept_plan`. The keyword router still builds the plan after that.

A string `evidence_requirements` is still invalid. The unit test `test_string_evidence_requirements_stay_invalid` covers that.

### Local planner evaluation

`python -m app.research.planner_eval`, real `gpt-4o-mini`, 24 labeled queries, empty evidence package, no specialist execution. The planner context still includes `candidate_agents` from the keyword router. Labels live in `app/research/planner_eval.py` and are not production rules.

| | Phase 9 staging | This local run |
| --- | --- | --- |
| Queries | 12 full research jobs | 24 planner-only calls |
| Malformed planner output | 12 | 0 |
| Valid plans (`source=llm`) | 0 | 24 |
| Retries | not recorded; identical provider retries did not recover | 0 |
| Fallback | 12 | 0 |
| Required-domain hits | not measured | 22 of 24 |
| Forbidden-domain selections | not measured | 1 |
| Unnecessary extra domains | not measured | 1 |

Misses, both still valid JSON:

- "What happened around a selected NVIDIA event and how did the stock behave afterward?" selected `market` and missed `news`.
- "What was the market reaction to the acquisition?" selected `news` and `market` and missed `company`.

"Tell me about the situation." selected `news`. The label marks every domain forbidden, so that one selection is the forbidden count and the unnecessary count. It is one query, not two.

### API

`GET /api/research/{id}` agent runs now include `attempt` (`retry_count + 1` on the stored row). The report body is unchanged in shape except citations may now include `publisher` and, for `market_fact`, a `market` object copied from the existing impact payload: symbol, windows, volume, volatility, and `configured_benchmark_symbol` from settings. The impact row does not store the benchmark ticker. `source_url` on a market citation stays null. No URL is invented.

### Frontend

`/` is the research desk. The previous case page is `/investigate`. The desk submits `POST /api/research`, polls `GET /api/research/{id}` every 2 seconds, and stops on `succeeded`, `partial`, `failed`, or `cancelled`, or after 120 polls. Unmount aborts the poll.

Shown from the payload: query, status, executive summary, findings with `FACT` / `OBSERVATION` / `INFERENCE` / `SCENARIO`, verification status, citations, http(s) source links, "Source URL unavailable" when the URL is null, market windows as returns versus the configured benchmark, excluded unsupported claims, limitations, and agent-run status. Confidence is not on the GET payload, so it is not shown. No buy, sell, or hold control.

Local browser check against `next dev` on port 3010: the form submits, the button disables while the request is in flight, and a down local API shows the error alert. At a 390px width, `scrollWidth` equaled `clientWidth`. A completed staging report was not rendered in that browser, because the local API was not running and the new images were not deployed.

### Tests

- `tests/unit/research` and `tests/unit/retrieval`: 113 passed.
- Focused planner, graph, master, routing, and package file run before that: 34 passed, included in the 113.
- Frontend `node --experimental-strip-types --test frontend/lib/researchDesk.test.ts`: 6 passed.
- `npx tsc --noEmit` and `npx next lint` on the research surface: no errors.
- Developer SQLite sha256 remains `d761c6c0b24c0253f4cdb36e4f26a991686869b52a1a3e9d14174be6d4872cc5`.

## Local observations

Strict schema removed the string-field failure mode on this 24-query local set. Zero retries means the first constrained response validated. That is not evidence the model will stay valid on every future query. The corrective retry and the keyword router are still in the path.

Domain selection is not perfect on that local set. Two labeled queries missed a required specialist, and one ambiguous query added news. Those plans were accepted because the names are in the enum. The router was not changed to force the labels.

The local eval used empty packages. It did not re-score claims, citations, or URL coverage.

## Staging deployment

Phase 10.1 pushed the existing images to the existing ECR repositories and updated the existing ECS services. VPC, ALB, RDS, Kafka, S3, the cluster, and the secret ARNs were left in place. Terraform was not applied.

The first combined push failed with `net/http: timeout awaiting response headers` while committing blobs. The tags were absent after that attempt. A later `docker push` of each tag completed. ECR `describe-images` digests:

| Image | Tag | Digest |
| --- | --- | --- |
| API | `225201316318.dkr.ecr.us-east-1.amazonaws.com/quantpilot-staging-api:phase10-api` | `sha256:7151ab9e185a20dab807f1dc6d8b3ed665e67b273ffb39a9094bb597ec6a139b` |
| Worker | `225201316318.dkr.ecr.us-east-1.amazonaws.com/quantpilot-staging-worker:phase10-worker` | `sha256:7151ab9e185a20dab807f1dc6d8b3ed665e67b273ffb39a9094bb597ec6a139b` |
| Frontend | `225201316318.dkr.ecr.us-east-1.amazonaws.com/quantpilot-staging-frontend:phase10` | `sha256:31bfb95e8f1a753c3489479ca74056e86568491e7f8e982ec5a7607ba22a1420` |

The API and worker tags are the same image. `latest` was not used.

ECS task definitions, after rollout `COMPLETED`, desired 1, running 1:

| Service | Task definition | Running digest |
| --- | --- | --- |
| `api` | `quantpilot-staging-api:6` | `sha256:7151ab9e185a20dab807f1dc6d8b3ed665e67b273ffb39a9094bb597ec6a139b` |
| `research-worker` | `quantpilot-staging-worker:11` | `sha256:7151ab9e185a20dab807f1dc6d8b3ed665e67b273ffb39a9094bb597ec6a139b` |
| `frontend` | `quantpilot-staging-frontend:3` | `sha256:31bfb95e8f1a753c3489479ca74056e86568491e7f8e982ec5a7607ba22a1420` |

A one-off task on `quantpilot-staging-worker:11` printed `PHASE10_PROOF master-v4 strict True evidence array corrective True attempts1 True` and exited 0. Its container digest matched the worker row above. Worker logs from this window do not contain `phase9-worker`.

`GET /health` and `GET /ready` on `http://quantpilot-staging-1583627039.us-east-1.elb.amazonaws.com` both returned HTTP 200. Ready reported database ok, revision `0013`, migrations current, Kafka ok, lake ok. Neo4j remained unconfigured. The responses did not include secret values.

The frontend document title is `QuantPilot research`. The research-desk bundle calls `http://quantpilot-staging-1583627039.us-east-1.elb.amazonaws.com`. It does not call `127.0.0.1`.

## Staging planner evaluation

Fifteen `POST /api/research` jobs, idempotency keys `phase101-01` through `phase101-15`, real `gpt-4o-mini`, worker `:11`, the existing Phase 9 corpus. Every master run was `master-v4`, `attempt` 1, `error` null, `plan.source` `llm`. None used the keyword fallback. The corrective retry was present in the image and was not exercised on this set.

Domain labels below are measurement labels for this note. They are not production routing rules.

| | Phase 9 deployed | This staging run |
| --- | --- | --- |
| Queries | 12 full research jobs | 15 full research jobs |
| Malformed planner output | 12 | 0 |
| Valid plans (`source=llm`) | 0 | 15 |
| Retry (attempt greater than 1) | not recorded; identical provider retries did not recover | 0 |
| Fallback | 12 | 0 |
| Required-domain hits | not measured | 14 of 15 |
| Queries that selected a forbidden domain | not measured | 2 of 15 |
| Forbidden domain picks | not measured | 4 |

Misses, all still valid JSON:

- `phase101-06` (`f1ab9c21-9a47-4ae8-b457-047787c4e863`), "How did NVIDIA perform around the recent news events in the corpus?", selected `news` and missed `market`.
- `phase101-11` (`7ce0cced-7276-429e-b546-42bc88225a06`), "What does Apple do?", selected `company`, `technology`, and `market`. The label required `company` and marked `technology` and `market` forbidden.
- `phase101-12` (`eca9ce3a-a840-4909-bb81-a9a770ee1f39`), "Tell me about the situation.", selected `news` and `market`. The label marks every domain forbidden.

## Staging research

All 15 jobs finished `succeeded`. Zero `partial`, zero `failed`.

| | |
| --- | --- |
| Claims | 264 |
| Supported | 228 |
| Uncertain | 32 |
| Unsupported | 4 |
| Conflicting | 0 |
| FACT | 170 |
| OBSERVATION | 61 |
| INFERENCE | 30 |
| SCENARIO | 3 |

Report citations: `document_excerpt` 93, `event_fact` 20, `market_fact` 20, `graph_fact` 0. Document and event citations with an https URL: 113 of 113. Null `source_url`: 20, all `market_fact`. Market citations stay outside the URL denominator. Checked market objects carry `configured_benchmark_symbol` `SPY`, the five session windows, and the method text "session return relative to the configured benchmark; not a causal effect". The impact row still does not store the benchmark ticker.

`GET /api/research/{id}` for these jobs includes `attempt` on every agent run. Document and event citations include `publisher` and `source_url`. Skipped specialists are still listed, with status `skipped`.

Traced `phase101-01` (`05a7920d-7577-4680-b4f3-5f68540eb450`): the fact "Nvidia has returned $46 billion to shareholders through dividends and buybacks in the first half of its fiscal year." cites document `a655ee7d6521af69`, The Motley Fool, `https://www.fool.com/investing/2026/09/17/nvidia-has-returned-46-billion-to-shareholders-thi`. That URL returned HTTP 200 and the page contains the $46 billion figure. The three market citations on that job are NVDA versus SPY, `source_url` null.

`phase101-10` (`e7462afd-f4f6-4189-9cdc-8a232bd51720`) asked about Microsoft partnerships. The plan selected `company`. The report has no `graph_fact` citation. One company finding restates an article line that Microsoft is an attractive investment alongside NVIDIA and Apple. That is a source sentence, not a relationship row. Zero relationships remains the corpus result.

`phase101-07` (`62295884-c072-4a0d-af17-6fe30066983f`) asked about Microsoft news and market performance. The plan selected `news` and `market`. Both stored market citations are NVDA versus SPY. The observations say "the stock" and do not name MSFT. The same NVDA windows appeared on a later browser rerun of that query (`72cf5986-c952-477b-8376-165a7035fd75`). The planner did not fall back. The retrieved market rows were not limited to the company named in the question.

Unsupported claims stayed out of the factual sections. `phase101-02` excluded "Microsoft's stock is rated as a Hold by Zacks Investment Research." The report limitations on that job are "unsupported claims were excluded from factual sections" and "investment language and unsupported causal wording were excluded". A supported fact on the browser rerun still restates a Zacks comparison headline that contains "Buy Now". The desk has no buy, sell, or hold control. Some source wording still lands in supported facts.

## Staging UI

Checked in the browser at the staging ALB, not the local dev server.

- The desk loads with the research input and Run research.
- Submit disables the button and changes it to "Research in progress". Status moved through Submitting and Queued, then "Research complete".
- The Microsoft report rendered FACT in `rgb(0, 255, 136)`, OBSERVATION in `rgb(0, 212, 255)`, INFERENCE in `rgb(255, 0, 255)`, supported in the same green, uncertain in magenta, and unsupported in `rgb(255, 51, 102)`.
- Three matrix jobs contain a SCENARIO claim. Neither browser rerun included one, so the SCENARIO stamp color was not observed on screen. The component maps SCENARIO to the readout color, which is a different class from the three stamps above.
- Open source links are real https URLs. The Microsoft dividend article opened in the browser at The Motley Fool and the title matched the citation. Market citations render "Source URL unavailable" and are not links.
- Market cards show "Relative return vs SPY" and "not a causal effect".
- The excluded Hold rating is under "Excluded from factual sections".
- Limitations and agent runs are visible, including `master succeeded attempt 1`.
- At 390, 768, and 1440 CSS pixels, `scrollWidth` did not exceed `clientWidth` on the empty desk or the completed report.

## Logs

CloudWatch groups `/quantpilot-staging/api` and `/quantpilot-staging/worker`, from about 40 minutes before the matrix through the completed jobs: zero events matched `sk-`, `AKIA`, `postgres://`, `Bearer `, or `password=`. Worker start lines include the `phase101-*` correlation id. The planner proof line is the only `master-v4` log hit in that window. Prompt text was not in the sampled job-start lines.

## Staging limitations

- Required-domain selection was 14 of 15, and two queries selected domains the measurement label marks forbidden. Valid JSON is not the same as the intended specialist set.
- The corrective retry did not run on these 15 jobs. This matrix does not measure whether a malformed response would recover.
- A Microsoft market question was answered with NVDA session windows. The observation text does not name the ticker.
- A company finding can restate investment language from an article. Unsupported ratings were excluded. Supported facts can still quote a headline that says buy.
- No graph relationships were created. The corpus still has none.
- SCENARIO was present on 3 stored jobs and absent from the two reports rendered in the browser.
- The desk does not show a live stage graph. Status and the completed `agent_runs` list are what the API returns.
- `configured_benchmark_symbol` is the setting at package-build time, not a column on `event_impacts`.
- This is not a capacity, HA, or causal result. RDS stays single-AZ. Kafka replication stays 1. HTTPS on the ALB is still not configured. Neo4j was not added.

## Commands

```text
python -m pytest tests/unit/research tests/unit/retrieval -q
python -m app.research.planner_eval
node --experimental-strip-types --test frontend/lib/researchDesk.test.ts
npx tsc --noEmit
npx next lint --dir app --dir components/research --dir lib
```
