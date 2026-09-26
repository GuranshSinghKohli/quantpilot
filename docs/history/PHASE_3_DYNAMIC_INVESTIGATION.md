# QuantPilot Phase 3 — Dynamic Investigation Orchestrator

**Scope:** Investigation workflow only. Company memory, Evidence Graph redesign, OpenClaw expansion, CrewAI, historical evaluation, and frontend redesign were not implemented.

**Source of truth:** Implementation. This document describes what the code does.

---

## 1. Before vs after

### Before (Phase 1/2)

```
Event / on-demand request
        ↓
investigation_runner.run_investigation
        ↓
Planner → sequential MCP collect (plus thin-API IR escalate)
        ↓
Hypothesis ranker → Verification → Devil's Advocate
        ↓
Persist Evidence Ledger + roster + notify
```

The runner was a linear Python coroutine. It did **not** decide whether to research again. LangGraph existed only for the separate deep-research report (`analysis_graph.py`).

### After (Phase 3)

```
Event / on-demand request
        ↓
investigation_runner (create row, detect move, noise skip — unchanged)
        ↓
LangGraph investigation_graph
        ↓
Planner → tasks → parallel collect → assess
        ↓
    enough? ──NO──► unused recommended tools and iteration < max
        │                    ↓
        │              identify gaps → collect again
        YES
        ↓
Rank → Verify → Devil's Advocate → synthesize
        ↓
investigation_runner persist + roster + notify (unchanged ledger)
```

The investigation **API shape is unchanged**. Persist still uses `replace_system_evidence` / `replace_system_claims`. Status strings in the database remain:

`planning | collecting | verifying | complete | failed | skipped_market_noise`

Richer names (`RESEARCHING`, `CHALLENGING`, `SYNTHESIZING`) appear only in in-memory graph state and structured logs.

`INVESTIGATION_GRAPH_ENABLED` (default `true`) can disable the graph and restore the linear runner.

---

## 2. LangGraph state

`InvestigationState` is a `TypedDict` in `backend/app/workflows/investigation_graph.py`.

It reuses existing objects instead of duplicating ledger models:

| Field | Source |
|---|---|
| `move` | Existing `MoveSnapshot` |
| `plan` | Existing planner dict (`focus`, `tools`, `seed_hypotheses`) |
| `research_plan` | New `ResearchPlan` dump |
| `hypotheses` | Planner seed strings |
| `hypothesis_rows` | Existing ranker/verify/DA hypothesis dicts |
| `evidence_items` | Existing evidence row dicts (`source_type`, `title`, `excerpt`, `source_url`, `retrieval_method`, `raw_payload`) |
| `tasks` | New `ResearchTask` dumps |
| `assessment` | New `EvidenceAssessment` dump |
| `ranked` / `verified` / `da` | Existing agent return dicts |
| `iteration_count` / `errors` / `metrics` | Graph bookkeeping |

Claims and evidence **rows in Postgres** are still `investigations` / `claims` / `evidence_items` / `claim_evidence_links`. The graph does not write SQL.

---

## 3. Graph nodes

| Node | Responsibility |
|---|---|
| `planner` | Calls existing `investigation_planner_agent.plan_investigation` via the runner module (so existing tests still patch). |
| `create_tasks` | Turns the plan into structured `ResearchTask`s. Does not fetch evidence. |
| `research` | Runs **pending** tasks through `investigation_runner._collect_evidence` (MCP only). Independent tools run concurrently. |
| `assess` | Deterministic `assess_evidence`. Decides sufficiency and unused next tools. |
| `identify_gaps` | Adds recommended unused tools as new pending tasks. |
| `rank` | Existing `hypothesis_ranker_agent.rank_hypotheses`. |
| `verify` | Existing `investigation_verification_agent.verify_hypotheses`. |
| `challenge` | Existing `devils_advocate_agent.stress_test_leading`. |
| `synthesize` | Marks status complete; final summary/persist stay in the runner. |

---

## 4. Conditional edges

```
START → planner → create_tasks → research → assess
                                      │
                    insufficient AND unused tools
                    AND iteration < MAX_INVESTIGATION_ITERATIONS
                                      │
                              identify_gaps → research
                                      │
                                 otherwise
                                      ↓
                         rank → verify → challenge → synthesize → END
```

Not every node runs unconditionally. Rank/verify/DA do **not** run until the assess router says evidence is sufficient **or** the loop cannot continue.

If the max iteration is reached with thin evidence, the graph **continues to verification** and sets `incomplete_evidence=true`.

---

## 5. Planner design

The planner still does **not** retrieve evidence.

It receives the Phase 2/8 move snapshot plus optional:

- `event` (type, magnitude, residual, z)
- `materiality_score`
- `portfolio_exposure`
- `asset_hint` / depth
- available MCP tool names

Output is the existing dict **plus** a structured `ResearchPlan`:

- `investigation_question`
- `hypotheses[]` (competing explanations, not a single answer)
- `tasks[]`
- `required_evidence` / `expected_sources`
- `stopping_conditions`
- `tools` (allowlisted MCP names only)

Malformed plans (no hypotheses, no tasks/tools) fail `validate_research_plan`. The graph falls back to `_fallback_plan` if the planner raises.

The planner does **not** emit every tool on every investigation. ETF plans drop filings/IR unless depth is `deep`. Equity 1d plans typically use news + filings + price, and IR only for large/downside/deep moves.

---

## 6. Research task model

```text
ResearchTask
  task_id          T1, T2, …
  category         news | filings | price | fundamentals | primary_source | …
  objective        one-line purpose
  hypothesis_ids   H1…Hn (all seeds until ranking)
  required_tool    MCP tool name
  priority         planner order
  status           pending | running | completed | failed
```

A failed task is recorded. It does not abort the investigation.

---

## 7. Tool routing

| Hypothesis / need | Tool category | MCP tool |
|---|---|---|
| Company/industry headlines | news | `get_stock_news` |
| Regulatory / disclosure | filings | `get_recent_filings` |
| Market reaction | price | `get_stock_price` / `get_price_history` |
| Earnings / valuation context | fundamentals | `get_stock_fundamentals` |
| Official IR / presentation | primary_source | `get_ir_materials` / `get_shareholder_letter` |

Agents never call `requests` / `httpx` / `yfinance` / SEC HTTP / browser APIs.

```
LangGraph node → investigation_runner._evidence_from_tool → MCP client → MCP tool
```

Browser MCP / existing OpenClaw capability is reached only when a task's `required_tool` is `get_ir_materials` or `get_shareholder_letter`. No new browser automation was added.

First graph collect pass uses `escalate_thin=False` so IR escalation is a **graph decision**, not a hidden collect side effect. Direct `_collect_evidence(...)` (Phase 10 tests, linear fallback) still escalates when API evidence is thin.

---

## 8. Evidence assessment

`backend/app/services/evidence_assessment.py` is deterministic. It does not invent facts.

`EvidenceAssessment` includes:

- `sufficient_evidence`
- `supported_hypotheses` / `weakened_hypotheses` (seed labels only — not a second ranker)
- `contradictions` (title/excerpt token pairs such as beat/miss)
- `missing_evidence`
- `recommended_next_tools` (unused allowlisted tools)
- `failed_tools`
- `evidence_counts`

Sufficiency bar (same spirit as the existing thin-API check):

- not thin **and** at least one non-price item, **or**
- ≥2 substantive news items, **or** ≥1 filing, **or** ≥1 IR page

---

## 9. Iterative research loop

```
research → assess → (gaps → research)* → rank
```

Hard cap: `MAX_INVESTIGATION_ITERATIONS` (default **2**, clamped 1–4).

The loop only continues when:

1. Evidence is insufficient, **and**
2. There is at least one **unused** recommended tool, **and**
3. `iteration_count < max`

This prevents infinite retries of the same failed tool.

On the cap: verification still runs; the runner appends “Evidence remains incomplete after the research loop.” to the summary.

---

## 10. Verification

Unchanged agent: `investigation_verification_agent.verify_hypotheses`.

FR-3 is still enforced after Devil's Advocate: hypotheses without `evidence_indices` are dropped before persist.

If the verify agent raises, the graph keeps only already-cited hypotheses and continues.

---

## 11. Devil's Advocate

Unchanged agent: `devils_advocate_agent.stress_test_leading`.

It still receives the leading hypothesis, competitors, and the same evidence pool. No second critic agent was added.

If DA raises, the graph records the failure and leaves the leading hypothesis unchallenged (`outcome=held`).

---

## 12. Persistence

No second database. No schema migration.

Persisted via existing helpers:

- investigation status / summary
- evidence rows (replace system-owned rows — idempotent on re-run)
- claims + evidence links
- verification notes + DA JSON
- roster JSON, now with additive `investigation_graph` (plan, assessment, iterations, metrics)

Re-running `/api/investigations/{id}/run` **replaces** system evidence/claims. It does not create a second investigation row.

---

## 13. Failure handling

| Failure | Behavior |
|---|---|
| Single MCP tool error | Evidence row with `raw_payload.error`; task `failed`; graph continues |
| Collect batch exception | One failure evidence item; continue |
| Planner exception | Fallback plan |
| Rank / verify / DA exception | Structured fallback; continue to persist when possible |
| Database / lookup error | Existing runner `mark_failed` path |
| Thin/missing evidence | Loop or continue with `incomplete_evidence` |

A failed research task does not automatically fail the investigation.

---

## 14. Retry strategy

- Transient tool errors (title contains `unavailable`/`failed` or `raw_payload.error`): retry up to `INVESTIGATION_TOOL_RETRIES` (default **1**) with a short backoff.
- Empty success (no headlines): not retried.
- Permanent failure after retries: record and continue.
- Invalid structured planner output: `validate_research_plan` / fallback plan. No infinite repair loop.
- Independent tools: `asyncio.gather` with `INVESTIGATION_RESEARCH_CONCURRENCY` (default **4**, max 8).

---

## 15. Observability

Structured `log_event` names:

- `INVESTIGATION_STARTED`
- `INVESTIGATION_PLANNED`
- `RESEARCH_TASK_CREATED` / `_STARTED` / `_COMPLETED` / `_FAILED`
- `EVIDENCE_ASSESSMENT`
- `ADDITIONAL_RESEARCH_REQUIRED`
- `VERIFICATION_STARTED`
- `DEVILS_ADVOCATE_STARTED`
- `INVESTIGATION_SYNTHESIS`
- `INVESTIGATION_COMPLETED`

Fields: `investigation_id`, `ticker`, `event_type`, `graph_node`, `task_id`/`tool`, `iteration`, `latency_ms` where measured. No secrets.

---

## 16. Testing strategy

`backend/tests/test_investigation_phase3_graph.py` covers:

- Valid / missing / malformed research plans
- Thin vs sufficient assessment, contradiction tokens, evidence dedupe
- Happy-path graph
- Insufficient-evidence loop (second collect uses filings)
- Max-iteration stop with incomplete evidence
- Failed tool does not abort
- Tool retry then success; permanent failure recorded
- Verification / DA reuse
- Investigate API persist
- Re-run same `investigation_id` does not duplicate claims/evidence

Existing Phase 8–13 investigation tests still patch runner attributes. The graph looks those up at call time on `investigation_runner`, so those patches still apply.

---

## 17. Example — NVDA −8.7%

What the **code** does for a large idiosyncratic drop (not a live market claim):

1. **Event received.** Sweep/gate (Phase 2) or on-demand `POST /api/investigations/investigate`. On-demand still **bypasses** the materiality gate. Runner creates one ledger row and detects the move (`MoveSnapshot`).
2. **Planner.** Seeds competing hypotheses (guidance, macro, regulatory, competitor, or the fallback set) and a **subset** of tools — typically news + filings + price; IR if the move is large/downside/deep and Browser MCP is enabled. It does not fetch pages.
3. **Research tasks created.** One `ResearchTask` per planned tool.
4. **Collect.** Pending tools run in parallel through MCP. Graph collect does **not** auto-escalate IR on this pass.
5. **Assess.** If only one headline and no filing/IR, evidence is insufficient.
6. **Gap.** Recommended unused tools (often `get_recent_filings`, `get_ir_materials`) become new tasks.
7. **Research again.** Second collect. Stops if still thin and `iteration == MAX_INVESTIGATION_ITERATIONS`.
8. **Rank.** Existing hypothesis ranker returns **support scores / weights**, not statistical probabilities.
9. **Verify.** Existing verifier drops uncited claims.
10. **Devil's Advocate.** Existing DA challenges the leader.
11. **Synthesis.** Runner builds the existing summary (move line + leading cause + DA outcome) and optional incomplete-evidence note.
12. **Persist.** Same Evidence Ledger tables. Additive `roster.investigation_graph` holds the plan, assessment, and iteration metrics.

---

## Configuration

| Variable | Default | Role |
|---|---|---|
| `INVESTIGATION_GRAPH_ENABLED` | `true` | Use LangGraph vs linear runner |
| `MAX_INVESTIGATION_ITERATIONS` | `2` | Hard research-loop cap |
| `INVESTIGATION_RESEARCH_CONCURRENCY` | `4` | Parallel MCP tool cap |
| `INVESTIGATION_TOOL_RETRIES` | `1` | Bounded retries for failed tools |

---

## What Phase 3 did not implement

- Company memory
- Expanded Evidence Graph / ledger redesign
- New OpenClaw or browser automation
- CrewAI / OpenAI Agents SDK
- Historical evaluation benchmark
- Major frontend redesign
- Autonomous monitoring beyond Phase 2
- A second persistence system
- Unbounded concurrency or infinite research loops
