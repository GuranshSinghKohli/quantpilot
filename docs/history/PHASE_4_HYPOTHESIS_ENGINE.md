# QuantPilot Phase 4 — Hypothesis Engine & Causal Investigation

**Scope:** Competing hypotheses, evidence requirements, support vs contradiction, interpretable scoring. OpenClaw, CrewAI, company memory, historical evaluation, Evidence Graph redesign, and frontend redesign were not implemented.

**Source of truth:** Implementation.

---

## 1. Purpose

Phase 3 decides *what to research next*. Phase 4 decides *which explanations are supported*.

The engine does not ask an LLM “why did NVDA fall?” and keep the first answer. It keeps a small set of competing hypotheses, records what would support or disprove each one, classifies collected evidence against those tests, and ranks **relative support**.

`support_score` is **not** a probability. The UI and summaries say so.

---

## 2. Hypothesis lifecycle

```
OPEN
  ↓ (evidence arrives)
INVESTIGATING / INSUFFICIENT_EVIDENCE
  ↓
SUPPORTED | WEAKENED | CONTRADICTED
  ↓ (still no support after evidence + missing requirements)
DISCARDED  (kept with a reason; not deleted)
```

Status is recomputed after every research pass. A discarded hypothesis can be revived if later evidence supports it.

Persisted claims remain the existing `claims` rows. Engine records are in-memory and copied into `roster.investigation_graph.engine`.

---

## 3. Hypothesis schema

In-memory `HypothesisRecord` (`backend/app/models/hypothesis_schemas.py`):

| Field | Meaning |
|---|---|
| `hypothesis_id` | `H1`…`H6` |
| `title` / `description` / `why_plausible` | Short explanation |
| `category` | Extensible taxonomy |
| `status` | Lifecycle above |
| `prior_support` / `current_support` / `support_score` | Relative scores 0–1 |
| `confidence_level` | `HIGH` / `MEDIUM` / `LOW` / `VERY_LOW` |
| `required_evidence` / `contradicting_requirements` / `missing_evidence` | Research tests |
| `supporting_evidence` / `contradicting_evidence` / `neutral_evidence` | Evidence **indices** |
| `tool_categories` | Feeds Phase 3 task linking |
| `discard_reason` | Audit trail |
| `stance` | Existing claim stance (`supports_move` / `market_noise` / …) |

No new database table. Ledger claims still store `statement`, `stance`, `confidence_score` (now the support score), `rank`, and evidence links.

---

## 4. Categories

`COMPANY_SPECIFIC`, `EARNINGS`, `GUIDANCE`, `MACRO`, `REGULATORY`, `COMPETITOR`, `INDUSTRY`, `MARKET_WIDE`, `TECHNICAL`, `SENTIMENT`, `OTHER`.

Unknown LLM categories fall back to keyword inference or `OTHER`.

---

## 5. Generation

`catalog_hypotheses` (deterministic) always runs in `QUANTPILOT_LIGHTWEIGHT` and as the LLM fallback.

`generate_hypotheses` may call the LLM once for titles/requirements, then validates: 3–6 items, known categories, required evidence present, duplicates dropped.

Cap: `MAX_HYPOTHESES` (default **6**).

The Phase 3 planner still chooses MCP tools. After seeds, it attaches `plan["hypothesis_records"]`. It does **not** become a second planner.

---

## 6. Research requirement model

Each hypothesis lists:

- evidence that would **support** it
- evidence that would **contradict** it
- `tool_categories` (`news`, `filings`, `price`, `primary_source`, …)

`ResearchTask.hypothesis_ids` now points at the hypotheses that need that tool, not always every `H1…Hn`.

Evidence path (unchanged graph, added references):

```
Evidence item
  → research task (required_tool + hypothesis_ids)
  → HypothesisRecord
  → investigation / claim
```

---

## 7. Evidence classification

Deterministic `classify_evidence_item` (keyword + source type + residual vs SPY):

| Relation | Meaning |
|---|---|
| `supports` | Text or price residual backs the hypothesis |
| `contradicts` | Text or residual works against it |
| `neutral` | Related source, no directional hit |
| `insufficient` | Tool failure / empty |

LLM ranking still writes narrative notes. It does **not** assign the numeric score.

---

## 8. Evidence quality

From existing `source_type` / `retrieval_method` — not invented reliability percentages:

| Quality | Sources |
|---|---|
| PRIMARY | SEC filing, IR page, transcript |
| HIGH | Price / MCP market data, macro |
| MEDIUM | News |
| LOW | Other / failed / unknown |

Notes are qualitative: “Strong evidence from a primary source.”

---

## 9. Evidence independence

`independence_group` clusters reprints:

- Primary sources are unique per host/title
- Reuters/AP-style wire hosts share a fingerprint
- Other news groups by host + normalized title

Repeats keep a `0.15` weight factor so five articles quoting one 8-K are not five confirmations.

---

## 10. Temporal relevance

Evidence timestamps come from `raw_payload` (`date`, `filing_date`, `providerPublishTime`, …).

Vs event time (event `occurred_at` / `detected_at`, else now):

| Label | Window | Causal weight |
|---|---|---|
| `pre_event` | calendar day before the event | 0.85 |
| `same_day` | same calendar day | 1.0 |
| `post_event` | 1–3 calendar days after | 0.55 |
| `retrospective` | later | 0.1 (not treated as causing the print) |
| `unknown` | no date | 0.5 |

This is timestamp preservation for a future evaluation harness. No benchmark was built.

---

## 11. Support scoring

```
support_score = clip(
    0.20
  + 0.20 × prior_support
  + Σ unique supporting weights
  − Σ unique contradicting weights
  − 0.07 × missing_required_count
, 0, 1)

weight = quality × temporal × (0.15 if reprint else 1)
```

`confidence_level` is a bucket of that score plus whether PRIMARY/HIGH support exists. It is **not** P(H).

---

## 12. Ranking

Sort by `support_score` desc, then supporting-count, then id.

Identify:

- `leading_hypothesis_id`
- `strongest_alternative_id` (next non-discarded)
- `weakest_hypothesis_id`

Existing `hypothesis_ranker_agent.rank_hypotheses` still runs (tests patch it). Engine scores overwrite `weight` / `confidence_score` when the real ranker runs. Overlay is additive when the ranker is mocked.

---

## 13. Dynamic updates

After every research node the graph calls `score_and_rank`. New evidence can:

- raise or lower support
- flip `SUPPORTED` → `WEAKENED` / `CONTRADICTED`
- create a new hypothesis if unexpected competitor/regulatory language appears and `len < MAX_HYPOTHESES`

Ranking is not locked until verification/synthesis.

---

## 14. Pruning

Low-support hypotheses with contradicting official language and missing required sources are marked `DISCARDED` with `discard_reason`. They stay in the engine list and roster snapshot.

---

## 15. Devil's Advocate

Same agent. Additive `hypothesis_context`:

- leading + strongest alternative
- supporting / contradicting evidence with source quality
- missing evidence
- support score / confidence level

DA now also returns optional: `overweighted_evidence`, `ignored_evidence`, `alternative_more_plausible`, `additional_evidence_needed`.

---

## 16. Verification

Same agent. Receives the same `hypothesis_context`. Still enforces FR-3 (no evidence → no claim). It does not re-score hypotheses.

---

## 17. Final synthesis

`_finalize_summary` still leads with the move, then:

- leading cause
- support level (and “not a probability” when a score is present)
- strongest alternative
- Devil’s Advocate outcome
- incomplete-evidence note from Phase 3 when applicable

Engine snapshot is stored under `roster.investigation_graph.engine`.

Claim–evidence links use `supports` or `contradicts` when the engine classified them. One link per claim/evidence pair (unique constraint unchanged).

---

## 18. Example — NVDA −8.7%

What the **code** does (catalog + engine; not a live market call):

**Event:** NVDA −8.7%, residual −7.7% vs SPY.

**Initial hypotheses (typical catalog):**

| Id | Title | Category |
|---|---|---|
| H1 | Guidance / earnings disappointment | GUIDANCE |
| H2 | Broad market / sector selloff | MARKET_WIDE |
| H3 | AI / industry demand concerns | INDUSTRY |
| H4 | Regulatory / export restriction | REGULATORY |
| H5 | Competitor announcement | COMPETITOR |
| H6 | Technical / profit-taking | TECHNICAL |

**Research requirements (examples):**

- H1 needs current/previous guidance, commentary, IR
- H2 needs SPY / sector move
- H4 needs filings / official statements

**After evidence:**

1. IR/8-K: “reduced next-quarter gross-margin guidance” → **supports H1** (PRIMARY)
2. Price residual −7.7% → **supports H1**, **contradicts H2**
3. News: “revenue beat, guidance unchanged” → **contradicts H1** → H1 may move to `WEAKENED` while remaining the leader
4. News: “competitor announced a new product” → may **create H5** if it was not already in the set
5. No regulatory primary source → H4 stays weak or `DISCARDED` with a reason

**Rank (illustrative):** H1 lead, H2 strongest alternative, H4/H6 weakest.

Devil’s Advocate receives that context and can still hold, cut confidence, or demote.

---

## Configuration

| Variable | Default | Role |
|---|---|---|
| `MAX_HYPOTHESES` | `6` | Hard cap on generated + discovered hypotheses |

---

## What Phase 4 did not implement

- Evidence Graph redesign
- OpenClaw expansion
- CrewAI
- Company memory
- Historical evaluation benchmark
- Frontend redesign
- New MCP tools
- A second research planner
- A second critic agent
- Fake probabilities
