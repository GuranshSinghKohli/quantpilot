# QuantPilot — Phase 4.2 Plan: Specialized LangGraph Research Agents

Status: **as-built with this step.**
Baseline: `6440968 feat: establish evidence and retrieval foundation` (Alembic head `0011`).

Phase 4.2 adds a **deterministic LangGraph** that consumes a Phase 4.1 EvidencePackage and emits structured, verified research findings. It does **not** add a Master Agent, autonomous planning, browsing, MCP research tools, LLM extraction into the graph, investment advice, or stock prediction.

```
ResearchRequest
      ↓
ContextAssembler  (Phase 4.1, unchanged)
      ↓
EvidencePackage
      ↓
LangGraph (fixed topology)
      ↓
News / Company / Technology / Market   ← approved tools only
      ↓
EvidenceAgent (deterministic verification)
      ↓
ResearchResult
```

The investigation graph (`workflows/investigation_graph.py`) and the ticker analysis graph (`workflows/analysis_graph.py`) stay as they are. This phase is a third, EvidencePackage-first path under `app/research/`.

PostgreSQL remains the system of record. pgvector remains a derived retrieval index. Neo4j is not queried by research agents. Agents never see database credentials, SQL, or Cypher.

---

## Vocabulary

| Concept | Meaning | Store |
|---|---|---|
| **SOURCE / DOCUMENT / EVIDENCE / CHUNK** | Unchanged Phase 4.1 meanings | existing tables |
| **EVIDENCE PACKAGE** | Bounded cited context | assembled, not a table |
| **AGENT FINDING** | Structured output of one specialist | in-memory, then optional `research_claims` |
| **CLAIM** | Finding after verification | `research_claims` |
| **RESEARCH RESULT** | Bounded API/job output | assembled; job row optional |
| **RETRIEVAL TOOL** | Approved wrapper over existing services | not a new data store |

These are not interchangeable. A finding is not evidence. Verification status is not confidence.

---

## Reuse (do not duplicate)

| Existing | Role in 4.2 |
|---|---|
| `ResearchRequest` + `assemble_package` | Only way to build agent context |
| `search` / structured loaders | Backing implementation of tools |
| `research_jobs`, `agent_runs`, `research_claims`, `claim_evidence` | Persistence (migration `0003`) |
| `ClaimKind` (`FACT` / `OBSERVATION` / `INFERENCE` / `SCENARIO`) | Finding and claim types |
| `VerificationStatus` | See mapping below |
| `EmbeddingProvider` pattern | Template for `LLMProvider` |
| `langgraph==0.6.11` | Already pinned; no second orchestrator |

`app/agents/llm.py` (`call_openai_json`) stays the investigation/analysis helper. Research uses a new `LLMProvider` so tests can inject `FakeLLMProvider` without touching investigation budget context vars.

No new Alembic revision. Existing CHECK constraints already cover claim kinds and verification statuses.

---

## Verification status mapping

The spec names `VERIFIED` / `PARTIALLY_SUPPORTED`. The shipped enum is reused:

| Spec name | Stored / API value | Meaning |
|---|---|---|
| VERIFIED | `supported` | FACT or OBSERVATION with valid citations and content check |
| PARTIALLY_SUPPORTED | `uncertain` | INFERENCE/SCENARIO with citations, or incomplete support |
| UNSUPPORTED | `unsupported` | Missing/wrong citations, or content check failed |
| CONFLICTING | `conflicting` | Contradictory cited statements; both kept |

Confidence never overrides this. `confidence=0.99` + `unsupported` stays unsupported.

INFERENCE and SCENARIO are never written as `supported`.

---

## Agent architecture

Fixed graph (LLM cannot add nodes or edges):

```
START
  → validate_request
  → build_evidence_package
  → run_specialized_agents   # news, company, technology, market (fixed order)
  → evidence_verification
  → finalize_result
  → END
```

Specialists do not mutate each other's findings. They write into `agent_findings[agent]`. Sequential execution (news → company → technology → market) keeps FakeLLM runs deterministic. The node is a conceptual fan-out.

No MasterAgent, PlannerAgent, or AutonomousResearchAgent.

| Agent | Responsibility | Must not |
|---|---|---|
| **News** | Summarize news documents and explicit events | Infer relationships from co-mention; invent quotes |
| **Company** | Identity, evidenced relationships, company events | Invent revenue, headcount, valuation, partnerships |
| **Technology** | What sources say a technology is; evidenced company links | Infer USES from a mention |
| **Market** | Window returns, volume, volatility from `event_impacts` | Predict, recommend, claim causation |
| **Evidence** | Citation existence, content check, conflicts, classification | Invent replacement evidence |

---

## ResearchState

Serializable dict (Pydantic models dumped to JSON-friendly values). No DB session, no raw documents, no embeddings.

```
request, evidence_package, agent_findings, claims,
verification_results, errors, metadata, result
```

Findings reference `citation_id` / `document_id` / `chunk_id` / `evidence_id`. The session and LLM provider travel in contextvars so the graph state stays serializable.

---

## EvidencePackage contract

Agents may reason only over package sections: entities, events, market_facts, graph_facts, documents, citations.

If they need more, they call an approved tool. Tools call `assemble_package` / `search` / structured loaders. Tools never contain SQL or Cypher.

A factual tool result without provenance is invalid for research use.

---

## Tools and permissions

| Tool | Backing service |
|---|---|
| `retrieve_context` | `assemble_package` |
| `get_entity` | `load_entities` |
| `get_entity_relationships` | `load_graph_facts` |
| `get_event` | `load_events` |
| `get_market_impact` | `load_market_facts` |
| `retrieve_documents` | `search` |
| `verify_evidence` | citation lookup in the current package |
| `retrieve_cited_evidence` | citation + linked package rows |

| Agent | Allowed tools |
|---|---|
| news | retrieve_documents, get_event, verify_evidence |
| company | get_entity, get_entity_relationships, get_event, retrieve_documents |
| technology | get_entity, get_entity_relationships, get_event, retrieve_documents |
| market | get_entity, get_event, get_market_impact, verify_evidence |
| evidence | verify_evidence, retrieve_cited_evidence |

`retrieve_context` is reserved for the graph's `build_evidence_package` node, not given to specialists (avoids unbounded re-assembly).

---

## Structured outputs

Primary output is `AgentFinding[]`, not prose.

| Field | Rule |
|---|---|
| `finding_type=FACT` | Directly supported by a source/evidence record |
| `OBSERVATION` | Measured/structured (market windows, recorded events) |
| `INFERENCE` | Interpretation of supported facts |
| `SCENARIO` | Conditional possibility |

A FACT with zero citations is rejected before it reaches `ResearchResult` (internal process notes are not findings).

---

## Hallucination guard (EvidenceAgent)

Always deterministic. Optional LLM assist is not used in tests and is not required for a status.

1. Every citation_id exists in the package.
2. FACT / OBSERVATION require ≥1 valid citation (or source lineage).
3. INFERENCE / SCENARIO require citations and stay `uncertain`.
4. Relationship FACT requires a `graph_fact` citation **or** a document excerpt that actually contains an explicit relationship verb. Co-occurrence is `unsupported`.
5. Opposite polarity statements with overlapping tokens → `conflicting`; both citations kept. No silent precedence.
6. Market findings containing buy/sell/hold/price target/expected return are dropped.
7. Market findings claiming the event *caused* the move are `unsupported`.
8. High confidence cannot promote a finding to `supported`.

---

## LLM provider

```
LLMProvider
  generate_structured(...)
  model_name / model_version
  token usage / latency / errors
```

| Setting | Default |
|---|---|
| `LLM_PROVIDER` | `openai` (tests force `fake`) |
| `LLM_MODEL` | `gpt-4o-mini` |
| `LLM_MODEL_VERSION` | `v1` |
| `LLM_TIMEOUT_SECONDS` | 30 |
| `LLM_MAX_RETRIES` | 3 |
| `LLM_INPUT_PRICE_PER_MTOK` / `LLM_OUTPUT_PRICE_PER_MTOK` | unset → cost unavailable |

Keys stay on `OPENAI_API_KEY` (`SecretStr`). No chat/completion embeddings. No external calls in unit tests.

`FakeLLMProvider` is deterministic and **not** a semantic-quality claim. Scripted scenarios: `valid`, `missing_citation`, `unsupported`, `conflicting`, `malformed`, `transient`, `rate_limit`, `timeout`, `permanent`.

---

## Prompts

`prompt_name` + `prompt_version` are recorded on every agent run (`news-v1`, `company-v1`, `technology-v1`, `market-v1`, `evidence-v1`). Changing wording requires bumping the version.

Raw prompts and raw LLM responses are not persisted.

---

## Retries and partial results

| Class | Examples | Behavior |
|---|---|---|
| Transient | timeout, rate limit, temporary provider error | Bounded exponential backoff |
| Permanent | auth, invalid structured output after retries | Fail that agent |

One specialist failure does not drop the others. EvidenceAgent still runs over successful findings. Failed agents appear as `errors` and `agent_metadata.status=failed`. Market failure → `market analysis unavailable`, never a fabricated MarketAgent finding.

---

## ResearchResult

Serializable. Bounded. Not a giant string.

```
request, findings, verified_claims, unsupported_claims, conflicts,
citations, agent_metadata, verification_summary, errors, created_at
unavailable_components
```

`unavailable_components` lists `semantic`, `llm`, or a failed agent name. Structured retrieval still works if pgvector is down (Phase 4.1). LLM down → research reports `llm_unavailable`; `/health` stays ok.

---

## API and jobs

`POST /api/research` accepts `ResearchRequest`, returns `ResearchResult`. Limits reuse Phase 4.1 ceilings. No SQL/Cypher/vectors in the response.

Execution lives in `app.research.service.run_research` so a later worker can call the same function. This phase is bounded and synchronous. No Kafka research topic.

Persistence (optional, default on when a session is provided): `research_jobs` + `agent_runs` + `research_claims`. `claim_evidence` is written only when `evidence_id` is a real `evidence_records` row. Full packages and prompts are not stored.

---

## Observability

`component=research`. Allowed extra labels: `agent`, `error_type`, `verification_status` (low cardinality). Forbidden: query, entity_id, event_id, document_id, claim_id, request_id, URL.

Metrics: `agent_runs_total`, `agent_failures_total`, `agent_duration_seconds`, `agent_tokens_total`, `agent_retries_total`, `research_runs_total`, `research_failures_total`, `claim_verification_total`, `unsupported_claims_total`, `conflicting_claims_total`.

Logs: operation, agent, model, prompt_version, status, duration, correlation_id. Never API keys, embeddings, full packages, full prompts, full LLM responses.

---

## Evaluation

Development set only (`python -m app.research evaluate`). FakeLLM + disposable SQLite. Not a semantic accuracy claim.

Measures: citation coverage, unsupported rate, verification accuracy on planted cases, structured-output validity, agent completion, partial-result rate. Claim precision/recall only where the fixture defines ground-truth statements.

---

## Security

Agents cannot execute SQL/Cypher, touch the filesystem or shell, read env/credentials, or call arbitrary URLs. Only the tool table above exists.

---

## Limitations

- FakeLLM extracts from package fields; it is not OpenAI quality.
- Relationship content check is lexical (verbs + names), not NLI.
- Conflict detection is polarity + token overlap, not a trained contradiction model.
- No Master Agent, no async job queue, no report renderer.
- Investigation and analysis graphs are unchanged and still do not consume EvidencePackage.
- Cost is unavailable unless both price settings are set; never labeled as billed amount.

---

## Testing (this phase)

Unit tests use disposable SQLite + FakeLLM. No OpenAI, no production DB, no Neo4j, no Kafka. Host suite, isolated Neo4j, Spark container (no manual Kafka overrides), `alembic check`, `npx tsc --noEmit`.
