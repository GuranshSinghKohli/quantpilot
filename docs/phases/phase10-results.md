# Phase 10 results — planner schema and research desk

## Facts

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

## Observations

Strict schema removed the string-field failure mode on this 24-query set. Zero retries means the first constrained response validated. That is not evidence the model will stay valid on every future query. The corrective retry and the keyword router are still in the path.

Domain selection is not perfect. Two labeled queries missed a required specialist, and one ambiguous query added news. Those plans were accepted because the names are in the enum. The router was not changed to force the labels.

## Limitations

- Staging was not updated. Local images exist: worker `quantpilot-staging-worker:phase10-worker` (manifest list `sha256:7151ab9e185a20dab807f1dc6d8b3ed665e67b273ffb39a9094bb597ec6a139b`) and frontend `quantpilot-staging-frontend:phase10` (manifest list `sha256:31bfb95e8f1a753c3489479ca74056e86568491e7f8e982ec5a7607ba22a1420`). ECR push was blocked in this session. Running services remain `quantpilot-staging-api:5`, `quantpilot-staging-worker:10` (`phase9-worker`), and `quantpilot-staging-frontend:2`.
- The 10–15 job staging research matrix was not run. Do not treat the local 24/24 as a staging result. Phase 9's 12/12 malformed figure is still the last measurement on the deployed worker.
- The local eval used empty packages. It did not re-score claims, citations, or URL coverage.
- `configured_benchmark_symbol` is the setting at package-build time, not a column on `event_impacts`.
- The research desk does not show a live stage graph. The job status and the completed `agent_runs` list are the stages the API returns.
- Not a production-capacity, HA, or causal result.

## Commands

```text
python -m pytest tests/unit/research tests/unit/retrieval -q
python -m app.research.planner_eval
node --experimental-strip-types --test frontend/lib/researchDesk.test.ts
npx tsc --noEmit
npx next lint --dir app --dir components/research --dir lib
```
