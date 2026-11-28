# LangGraph workflow (Phase 4.3)

Topology is fixed in code. The LLM cannot add nodes or edges.

```
START
  → validate_request
  → build_evidence_package
  → create_research_plan
  → run_relevant_specialists   # only the planned subset, fixed order
  → collect_findings
  → verify_claims
  → resolve_conflicts
  → assemble_report
  → finalize_result
  → END
```

Implementation: `app/research/graph.py` using the already-pinned `langgraph==0.6.11`.

## State

`ResearchState` is a JSON-serializable dict:

`request`, `evidence_package`, `plan`, `agent_findings`, `claims`, `verification_results`, `errors`, `metadata`, `report`, `result`

The database session and LLM provider are contextvars (`app/research/runtime.py`), not state fields. Findings reference citation/document/chunk/evidence IDs, not raw document bodies.

## Specialists

The specialist node runs only agents named on the `ResearchPlan`, in fixed order (`news` → `company` → `technology` → `market`). Others are stored as `skipped`. FakeLLM tests stay deterministic because the order does not change.

Retries: transient timeout / rate-limit / temporary provider errors use bounded exponential backoff (`LLM_MAX_RETRIES`). Permanent auth or malformed output after retries fail that agent only.

## Prompts

`master-v4`, `news-v2`, `company-v2`, `technology-v2`, `market-v2`, `evidence-v2`, `report-v1`. Change the version string when wording changes. Raw prompts and raw completions are not persisted. The report prompt may only repeat statements already on the verified set.

## Later jobs

`run_research(db, request)` is independent of FastAPI. Phase 4.4's worker calls that function. The HTTP handler does not. See `docs/PHASE4_4_PLAN.md`.
