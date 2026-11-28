# Phase 10 plan — planner reliability and research desk

## Root cause

Phase 9 stored `error: malformed` on the master row for 12 of 12 jobs. The provider already retried that error up to `LLM_MAX_RETRIES` with the same messages. The raw body was not stored.

A local reproduction with the same `gpt-4o-mini` call, `response_format: json_object`, `master-v3`, and `MasterPlanPayload` showed the mismatch. On 12 of 15 substantive queries the model returned `evidence_requirements` as one prose string. On 3 of those 15, `requested_sections` was a string as well. `selected_agents` was usually a list of allowed names. Empty or ambiguous queries returned empty lists and validated.

`MasterPlanPayload` sets `extra=forbid` and types `evidence_requirements` as `list[str]`. A string fails validation, the error type is `malformed`, and `accept_plan` never runs. The keyword router then selects specialists. That router stays.

`json_object` mode asks for JSON. It does not enforce field types. The existing coerce only joined a list of `reasoning_notes` into a string. It does not wrap a string into a list, and this phase does not add that wrap. Turning the sentence into a one-item list would make the schema pass without the model emitting the contract.

## Fix

- Planner calls use OpenAI strict `json_schema` for `MasterPlanPayload`: arrays for agents, evidence requirements, and sections; agent and section names are enums; `additionalProperties` is false.
- If the API rejects that response format, the provider retries the same attempt once with `json_object`.
- The graph makes one corrective call when the first result is `malformed` or `invalid_plan`. The second user message includes the validation hint. A third planner call is not made. Auth and transport errors are not given that corrective retry.
- `master-v4` states that those three fields are arrays.
- Unknown agent names still fail `accept_plan`. The fallback router still builds the plan.

## What this phase does not change

No new provider, queue, database, or agent framework. Neo4j stays unconfigured. The keyword router stays. Verification, citation URL rules, and the non-causal market wording stay. The research page reads `POST /api/research` and `GET /api/research/{id}`.
