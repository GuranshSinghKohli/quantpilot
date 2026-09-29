# Phase 4.6 — Retrieval date correctness and planner precision

Phase 4.5 is unchanged at `5ae5e81cd60c9a3daa61ea6614a9974b97b61776`. Alembic head stays `0012`. This phase does not add a migration, an agent, or a worker redesign.

Scores below are fixture checks. Lexical retrieval numbers are not semantic search quality. Planner numbers are not human-judged intelligence.

## What Phase 4.5 measured

Retrieval, 8 lexical queries, inside `--suite all`:

- recall@5 = recall@10 = 0.9375
- precision@5 = 0.25, precision@10 = 0.125
- MRR = 1.0
- empty rate = 0.0, duplicate rate = 0.0
- citation coverage = 1.0
- entity-filter accuracy = 1.0
- event-filter accuracy = 1.0
- date-filter accuracy = 0.0

Planner unnecessary-agent rate, using `selected agents minus relevant_agents`:

- 0.5541 on the 24-case baseline
- 0.5484 on the 50-case set

Plan validity, specialist selection, and fallback correctness were 1.0. Those historical numbers stay in the report as `unnecessary_agent_rate`.

## Date-filter root cause

The SQL comparison was not dropping in-range rows. An isolated retrieval run, with only the March 1 retrieval fixture loaded, scored date-filter accuracy 1.0 under the old probe.

`--suite all` seeds research documents dated 2026-04-01 and then runs retrieval on the same session. The old probe asked for `published_at >= 2026-03-02` and treated any hit as failure. The April rows are inside that open-ended bound, so the score was 0.0.

Two implementation gaps remained, and this phase closes them:

- Both ends of the window were inclusive.
- SQLite returns `published_at` without an offset. Callers were not required to pass UTC.

The evaluator was not flipped to ignore hits. The probe is now a set of closed windows on known timestamps. A later fixture row cannot satisfy an earlier window.

## Date semantics

One helper, `app.retrieval.time`:

- Aware input converts to UTC.
- Naive input from a caller raises `NaiveTimestamp`.
- A value read from SQLite is treated as UTC, because the offset is not stored.
- Serialized instants stay ISO 8601.

The window is half-open: `start <= timestamp < end`.

A date-only range uses UTC civil days. `2026-01-01` through `2026-01-02` is `[2026-01-01 00:00Z, 2026-01-03 00:00Z)`. The second day is included. `2026-01-03 00:00Z` is not.

## Which timestamp is authoritative

| Filter | Column | Not used as the filter |
| --- | --- | --- |
| Document publication | `published_at` | `retrieved_at`, ingestion time |
| Event occurrence | `occurred_at` | the document's `published_at` |

A missing `published_at` does not match a publication window. An event can occur on a different day from the article that reports it. Those columns stay distinct. The same `date_from` / `date_to` bounds are passed to both filters; each filter reads its own column.

## Planner root cause

`select_agents` used to concatenate the query with document titles, excerpts, event titles, and graph relationships, then substring-match that blob. It also added news when any document existed, company when any entity existed, and market when any market row existed. Entity-scoped fixture requests therefore selected extra specialists. That is why the Phase 4.5 rate sat near 0.55. It was not an LLM choosing extra agents. FakeLLM copies `candidate_agents` from this router.

The router now tokenizes the request. Package text does not add a specialist. A partnership request still selects news and company, because the request asks for a partnership. A GPU mention inside a stored article does not select technology unless the request asks for it.

An agent is required when its domain is needed to answer. An agent is optional when it can add context the request does not demand. An agent is forbidden when the request can be answered without that domain. `relevant_agents` was often a single preferred tag. Those tags were not rewritten, so the old rate remains visible. Where the tag was narrower than the request, the new required/optional/forbidden label records the correction. Example: "NVIDIA GPU demand news" needs news and technology. The old tag listed only news.

Routing reasons stored on a fallback plan are short sentences such as "Market movement requested." They are not a chain of thought.

`RESEARCH_MAX_AGENTS` stays 4. Multi-agent requests stay valid.

## Fixture routing metrics

These names mean fixture-based routing correctness:

- `required_agent_recall` — share of required agents that were selected
- `forbidden_agent_precision` — share of selected agents that were not forbidden
- `objective_unnecessary_agent_rate` — selected agents that are neither required nor optional, divided by selected agents
- `optional_agent_usage` — share of optional agents that were selected
- `unnecessary_agent_rate` — the Phase 4.5 definition, kept for comparison

The ten hand-written requests live in `ROUTING_CASES` and are reported as `routing_examples`.

## Measured after the fix

Retrieval, including inside `--suite all` (the session that used to score 0.0):

- date-filter accuracy = 1.0 (Phase 4.5 `--suite all` was 0.0)
- recall@5 = recall@10 = 0.9375
- precision@5 = 0.25, precision@10 = 0.125
- MRR = 1.0
- empty rate = 0.0, duplicate rate = 0.0
- citation coverage = 1.0
- entity-filter accuracy = 1.0
- event-filter accuracy = 1.0

`unnecessary_agent_rate` is still `selected minus relevant_agents`:

- 24-case baseline: 0.2000 (Phase 4.5 was 0.5541)
- 50-case set: 0.1818 (Phase 4.5 was 0.5484)

The drop is the router no longer reading package text. The remainder is the old tag being narrower than the request. Example: some partnership cases list only `news` in `relevant_agents`, and the router also selects `company`.

Fixture routing on the 50-case set:

- required-agent recall = 1.0
- forbidden-agent precision = 1.0
- objective unnecessary-agent rate = 0.0
- optional-agent usage = 0.0

The ten hand-written requests:

- required-agent recall = 1.0
- forbidden-agent precision = 1.0
- unnecessary-agent rate = 0.0
- optional-agent usage = 1.0 (the partnership-list request selects news, which is optional)

Plan validity, specialist selection, and fallback correctness stay 1.0.

Claim type recall moved because two queries no longer run the specialist that used to emit the fixture FACT:

- 24-case claim recall 0.8824, F1 0.9375 (Phase 4.5 was 1.0 / 1.0)
- 50-case claim recall 0.9167, F1 0.9565 (Phase 4.5 was 1.0 / 1.0)
- claim precision stays 1.0

"AMD Instinct Meta data centers" has no domain vocabulary, so no specialist runs. "Meta data center accelerators" selects technology. The technology fixture reader does not treat that article excerpt as a technology fact, and the news specialist is not selected. Expected labels were not edited to hide this.

## Known limitations

- Queries with no domain vocabulary select no specialist. "AMD Instinct Meta data centers" is one of those. The old tag said news because the package mentioned an announcement. The router no longer reads that package.
- A live model may still return any allowed specialist list. Invalid names and oversized lists fall back to the deterministic router. The master prompt tells the model to prefer `candidate_agents`.
- Lexical embeddings remain a development hasher.
- No capacity claim. No migration.

## Deferred

Phase 4.7 is not started. Load testing stays deferred. Specialist agents, the outbox worker, and the Kafka topics are unchanged.
