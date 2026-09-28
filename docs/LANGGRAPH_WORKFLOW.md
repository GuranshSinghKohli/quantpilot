# LangGraph workflow (Phase 4.2)

Topology is fixed in code. The LLM cannot add nodes or edges.

```
START
  → validate_request
  → build_evidence_package
  → run_specialized_agents    # news, company, technology, market (fixed order)
  → evidence_verification
  → finalize_result
  → END
```

Implementation: `app/research/graph.py` using the already-pinned `langgraph==0.6.11`.

## State

`ResearchState` is a JSON-serializable dict:

`request`, `evidence_package`, `agent_findings`, `claims`, `verification_results`, `errors`, `metadata`, `result`

The database session and LLM provider are contextvars (`app/research/runtime.py`), not state fields. Findings reference citation/document/chunk/evidence IDs, not raw document bodies.

## Specialists

The specialist node is a conceptual fan-out. Execution is sequential (`news` → `company` → `technology` → `market`) so FakeLLM tests are deterministic. Agents write only to `agent_findings[agent]`.

Retries: transient timeout / rate-limit / temporary provider errors use bounded exponential backoff (`LLM_MAX_RETRIES`). Permanent auth or malformed output after retries fail that agent only.

## Prompts

`news-v1`, `company-v1`, `technology-v1`, `market-v1`, `evidence-v1`. Change the version string when wording changes. Raw prompts and raw completions are not persisted.

## Later jobs

`run_research(db, request)` is independent of FastAPI. A future worker can call the same function. This phase does not add a Kafka research topic.
