# QuantPilot — Phase 4.3: Bounded master research and verified report assembly

Phase 4.3 adds orchestration and report assembly on the Phase 4.2 research graph. It does not replace the specialists, the EvidencePackage, or the deterministic verification guard.

## What shipped

A fixed LangGraph topology in `app/research/graph.py`:

```
validate_request
  → build_evidence_package
  → create_research_plan
  → run_relevant_specialists
  → collect_findings
  → verify_claims
  → resolve_conflicts
  → assemble_report
  → finalize_result
```

The model cannot add or remove nodes, create tools, or choose an agent name outside `news`, `company`, `technology`, `market`.

`ResearchPlan` (`plan-v1`) records the objective, the normalized request, the selected agents, evidence requirements, requested sections, and whether the plan came from the model or the documented fallback router. The fallback uses keyword and package signals already present in retrieval (documents, events, entities, graph edges, market facts). It does not invent an agent. If the planner output names an unknown agent or exceeds `RESEARCH_MAX_AGENTS` / `RESEARCH_MAX_REPORT_SECTIONS`, the run uses that fallback and still completes.

Specialists are the Phase 4.2 agents. An agent that the plan does not select is recorded as `skipped` and is not called. One specialist failure keeps the other findings, records the failure, and marks the job `partial`.

Verification still runs before the report. Status values stay `supported`, `uncertain`, `unsupported`, `conflicting`. `INFERENCE` and `SCENARIO` stay uncertain. Unsupported claims stay out of factual sections. Conflicting claims are both kept, with both citations. Nothing in this phase ranks sources.

`ResearchReport` is assembled in code from the verified claim set. The model may only repeat statements that already passed verification. Bullets that do not match a supported fact or observation are dropped and counted. Empty sections are omitted. The executive summary cannot introduce a new factual claim, a buy/sell/hold line, a price target, or a causal "caused" claim.

`ResearchResult` now includes `research_id`, `status` (`succeeded` / `partial` / `failed`), `plan`, and `report`. `succeeded` is the stored form of a completed run. `POST /api/research` is unchanged.

## Persistence

No new migration. Head remains `0011`.

Existing tables:

- `research_jobs` — status, including `partial`
- `agent_runs` — master, selected specialists, skipped specialists, evidence
- `research_claims` and `claim_evidence` when an evidence ref is a real `evidence_records.id`
- `reports` — plan and report JSON in `sections`

Repeating the same idempotency key does not insert a second set of claims.

## Limits

`RESEARCH_MAX_AGENTS`, `RESEARCH_MAX_FINDINGS`, `RESEARCH_MAX_CLAIMS`, `RESEARCH_MAX_EVIDENCE`, `RESEARCH_MAX_REPORT_SECTIONS`, `RESEARCH_MAX_SUMMARY_ITEMS`.

## Observability

Low-cardinality counters: `research_requests_total`, `research_completed_total`, `research_partial_total`, `research_failed_total`, `research_plan_failures_total`, `research_agent_failures_total`, `research_verification_conflicts_total`, `research_unsupported_claims_total`, `research_report_validation_failures_total`. Durations use `research_duration_seconds` with `operation` of `research`, `plan`, `specialist`, `verify`, or `report`. Labels stay in the existing allowlist (`agent`, `status`, `finding_type`, and the other closed keys). Query text, tickers, and research ids are not labels.

## Explicitly not in this phase

- Autonomous browsing, arbitrary SQL, Cypher, filesystem, shell, or credential access
- Dynamic tools or dynamic graph nodes
- Investment recommendations or causal claims
- An async research worker or a Kafka research topic
- A new research HTTP route
- Semantic quality claims from `FakeLLMProvider`

`run_research()` is callable without FastAPI so a later worker can use it. That worker is not built here.
