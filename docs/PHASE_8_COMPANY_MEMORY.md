# QuantPilot Phase 8 — Company Memory & Longitudinal Financial Intelligence

**Scope:** Persistent, source-backed, company-level memory across investigations. Historical evaluation, frontend redesign, CrewAI, LangGraph replacement, OpenClaw expansion, Evidence Graph redesign, trading, and portfolio optimization were **not** implemented.

**Source of truth:** Implementation.

---

## 1. Purpose

Investigations no longer start from a blank page every time.

```
COMPANY MEMORY
    ├── financial / event history
    ├── management statements
    └── previous investigations
            ↓
        NEW EVENT
            ↓
      INVESTIGATION
            ↓
    COMPARE WITH MEMORY
            ↓
   NEW COMPANY INTELLIGENCE
```

The system can say: last quarter management said X; today they said Y; that is a **change**. It does not automatically claim the change caused the print.

---

## 2. Memory architecture

Hybrid. No second vector database. No second evidence database.

| Layer | Store | Role |
|---|---|---|
| Structured | PostgreSQL / SQLite `company_memory_items` | Facts, statements, risks, events, conclusions, themes |
| Semantic | Existing Chroma `investigation_ledger` | Related prior investigations (already owner-scoped) |
| Provenance | Existing Evidence Ledger | `evidence_id` / `investigation_id` on every stored item |

Do **not** dump full reports into a new Chroma collection and call it company memory.

---

## 3. Structured memory

`CompanyMemoryItem` is append-only and owner-scoped (`owner_key`).

Fields that matter: `ticker`, `memory_type`, `statement`, `theme_key`, `status`, `polarity`, `observed_at`, `last_seen_at`, `observation_count`, `fingerprint`, `evidence_id`, `investigation_id`, `source_url`, `supersedes_id`.

Existing `investigations` / `claims` / `evidence_items` remain the source of truth. Memory **derives** from them after a case completes.

---

## 4. Semantic memory

Reuse `investigation_chroma` / `investigation_search` for historical case retrieval.

Company-memory *items* are queried from SQL with relevance ranking (ticker, theme, event, hypotheses). Chroma is not replaced.

---

## 5. Memory types

| Type | Example |
|---|---|
| COMPANY_FACT | “Revenue increased 12%.” |
| MANAGEMENT_STATEMENT | “Management expects continued strong AI demand.” |
| RISK | “Export restrictions are a recurring regulatory risk.” |
| EVENT | “NVDA experienced an 8.7% decline on 2026-08-20.” |
| INVESTIGATION_CONCLUSION | Leading grounded statement from a completed case |
| THEME | Recurring theme_key across observations |

---

## 6. Provenance

A memory item is stored only if it has:

- a timestamp (`observed_at`)
- `investigation_id` and/or `evidence_id`
- company-specific, non-generic text

Unsupported LLM text is not stored. Portfolio / user-private language is rejected.

---

## 7. Temporal memory

Historical statements are **not overwritten**.

Q1 / Q2 / Q3 management comments remain as separate rows (or confirmed observations). When polarity flips, the prior row is marked `OUTDATED` and the new row `supersedes_id` points at it.

---

## 8. Management statement tracking

Theme keys include `ai_demand`, `margins`, `guidance`, `export_restrictions`, `supply_constraints`, and similar.

Polarity: POSITIVE / NEGATIVE / NEUTRAL from wording (strong vs cautious / lowered).

Example:

- Q1: “AI demand remains strong.” (POSITIVE)
- Q2: “AI demand remains strong.” (POSITIVE, new row or confirm)
- Q3: “Customers are becoming more selective.” (NEGATIVE)

**Management trend:** “Demand commentary has weakened.”

That is contextual evidence, not a causal claim.

---

## 9. Deduplication

Same `owner_key` + ticker + type + fingerprint → **CONFIRM**: increment `observation_count`, append observation date, keep one row.

“Export restrictions are a risk” repeated three times is one persistent risk with three observations.

---

## 10. Memory updates

Statuses: `CREATE` (ACTIVE), `CONFIRM`, `WEAKEN`, `CONTRADICT`, `OUTDATED`, `ARCHIVE`.

History is preserved. Rows are not silently deleted.

---

## 11. Conflict detection

Memory: “Management expects margins to improve.”  
Current extract: “Management lowered margin guidance.”

The system:

1. keeps the historical statement
2. stores the new statement
3. marks the previous expectation `OUTDATED`
4. records `MEMORY_CONFLICT_DETECTED`

Current investigation evidence always wins when planning/verifying.

---

## 12. Historical investigation retrieval

`retrieve_for_investigation` returns relevant items plus `related_investigation_ids` (completed cases for that ticker, owner-scoped).

Not “return the entire history.” Ranked by theme overlap with the current event / hypotheses.

---

## 13. Similar event detection

If |current move − historical move| ≤ 3 percentage points and both are material:

> Similar historical event: NVDA −9.2% (not the same event).

Never “same event.”

---

## 14. Longitudinal themes

When a `theme_key` appears across multiple observations, a `THEME` row is stored (e.g. export restrictions across three cases). No dashboard in this phase.

---

## 15. Memory → LangGraph

```
EVENT → LOAD COMPANY MEMORY → PLANNER → … → VERIFY → DA → SYNTHESIZE
                                         ↓
                              extract_and_store (after persist)
```

`load_memory` is fail-open. Planner prompt includes historical context and an explicit rule: **new evidence wins**.

Hypothesis / verification context receives `historical_memory_note`: memory is not current-event evidence and cannot support a new causal claim.

---

## 16. User isolation

Every query filters `owner_key`.

- Company research for User A never appears in User B’s memory.
- Portfolio weight / “% of this user’s holdings” is never stored and is stripped before the planner.

Additive APIs (owner-scoped):

- `GET /api/investigations/memory/{ticker}`
- `GET /api/investigations/memory/{ticker}/related`

Existing `/api/memory/*` report endpoints are unchanged.

---

## 17. Testing

`backend/tests/test_investigation_phase8_company_memory.py` covers creation, provenance, temporal preserve, dedup, status updates, conflicts, retrieval, similar events, user isolation, planner injection, graph load, and additive APIs.

Events (no secrets): `MEMORY_RETRIEVAL_STARTED`, `MEMORY_RETRIEVAL_COMPLETED`, `MEMORY_ITEM_CREATED`, `MEMORY_ITEM_UPDATED`, `MEMORY_ITEM_DEDUPLICATED`, `MEMORY_CONFLICT_DETECTED`, `MEMORY_CONTEXT_ADDED`, `MEMORY_EXTRACTION_COMPLETED`.

---

## 18. Example company memory

**COMPANY:** NVDA

**Historical**

| When | Statement | Polarity |
|---|---|---|
| Q1 2026-02 | “AI demand remains strong.” | POSITIVE |
| Q2 2026-05 | “AI demand remains strong.” | POSITIVE |
| Q3 2026-08 | “Customers are becoming more selective.” | NEGATIVE |

**Management trend:** Demand commentary has weakened.

Q1/Q2 rows remain. Q3 does not overwrite them.

**Flagship**

Investigation #1: NVDA drop → memory stores “Export restrictions are a recurring risk.”

Investigation #2 (later): another drop → retrieval surfaces that risk and a similar-magnitude prior event.

The synthesis may say the new case *resembles* a previous regulatory investigation, with current evidence taking precedence. It does not invent causality.

---

## What Phase 8 did not implement

- Historical evaluation (Phase 9+)
- Frontend redesign / memory dashboard
- CrewAI
- LangGraph replacement
- OpenClaw expansion
- Evidence Graph redesign
- Trading / portfolio optimization
- A generic memory chatbot
- A second vector database
