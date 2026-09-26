# QuantPilot Phase 5 — Evidence Graph & Auditable Evidence Intelligence

**Scope:** Strengthen the existing Evidence Ledger. OpenClaw, CrewAI, company memory, historical evaluation, frontend redesign, and a second evidence store were not implemented.

**Source of truth:** Implementation.

---

## 1. Purpose

Phase 5 moves QuantPilot from “here are some sources the AI used” to an auditable chain:

```
INVESTIGATION → CLAIM → EVIDENCE → SOURCE → PROVENANCE → QUALITY / RELEVANCE → SUPPORTS | CONTRADICTS | CONTEXT
```

The system can answer **“Why does QuantPilot believe this?”** with persisted evidence, not with an LLM’s self-reported confidence.

Generated text may **summarize** evidence. It is never itself evidence.

---

## 2. Existing Evidence Ledger

Phase 5 reuses the Phase 7 ledger. There is still one store:

| Table | Role |
|---|---|
| `investigations` | Case row |
| `evidence_items` | One retrieved source unit |
| `claims` | Ranked statements / hypotheses |
| `claim_evidence_links` | Unique `(claim_id, evidence_id)` with `supports` / `contradicts` / `context` |

No second database, no graph database, no CrewAI memory.

What already existed and was kept:

- `source_type`, `retrieval_method`, `title`, `excerpt`, `source_url`, `raw_payload_json`, `created_at`
- Claim `statement`, `stance`, `confidence_score`, `rank`, `devil_advocate_notes`
- Link relation vocabulary: `supports | contradicts | context`
- GET/POST investigation, evidence, claims, links

---

## 3. Evidence architecture

Intelligence is a **deterministic layer** on top of the ledger:

```
collect (MCP)
    ↓
dedupe_items + enrich_item     (hash, group, provenance)
    ↓
Phase 4 hypothesis engine      (relative support_score)
    ↓
build_snapshot                 (claim assessments, conflicts, gaps)
    ↓
verification + Devil's Advocate
    ↓
replace_system_evidence / replace_system_claims
    ↓
GET investigation              (_to_detail recomputes assessments)
```

Code:

- `backend/app/services/evidence_graph.py` — hashing, taxonomy, support, conflicts, citations
- `backend/app/models/evidence_graph_schemas.py` — in-memory types
- `backend/app/services/evidence_ledger_store.py` — persist / expose additive fields

---

## 4. Evidence schema

Persisted columns (minimal additions):

| Column | Table | Why |
|---|---|---|
| `content_hash` | `evidence_items` | SHA-256 of normalized body for exact-content dedup |
| `claim_type` | `claims` | FACT / INTERPRETATION / CAUSAL_EXPLANATION / RISK / CONCLUSION |

Everything else that Phase 5 needs is stored in `raw_payload.provenance` and **projected** onto `EvidenceItemOut` as additive fields:

`source_name`, `published_at`, `retrieved_at`, `content_hash`, `source_quality`, `source_class`, `source_taxonomy`, `temporal_relevance`, `source_group`, `source_tier`

Existing `source_type` values are unchanged (`filing`, `ir_page`, `transcript`, `news`, `price`, `macro`, `options`, `other`).

`retrieval_method` may be `browser_mcp` so Phase 6 can persist browser-acquired evidence later. OpenClaw was not expanded.

---

## 5. Claim schema

Persisted: existing fields plus `claim_type`.

Computed on read (not extra tables):

| Field | Meaning |
|---|---|
| `support_status` | UNSUPPORTED / PARTIALLY_SUPPORTED / SUPPORTED / STRONGLY_SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE |
| `support_assessment` | Interpretable counts: supporting, contradicting, context, independent groups, primary count, relevance, temporal, conflict, gaps |

A claim is never labeled `true`. Causal claims top out at **supported**, not proven.

Claim types:

| Type | Example |
|---|---|
| FACT | “Revenue increased 12%.” |
| INTERPRETATION | “Growth remained strong.” |
| CAUSAL_EXPLANATION | “Guidance concerns likely contributed to the decline.” |
| RISK | “Export rules are a downside risk.” |
| CONCLUSION | Rare; inferred only for explicit “leading” language at rank 1 |

---

## 6. Claim / evidence relationships

Reuse `supports | contradicts | context`. Neutral maps to `context`. One relation per pair; if both support and contradict are supplied, **contradicts wins**.

Low-relevance support (for example a price print against a guidance claim) does **not** count toward support unless the source is PRIMARY.

---

## 7. Provenance

Every enriched item records:

- where it came from (`source_name`, `source_url`, `source_type`, taxonomy, class)
- when it was published (`published_at` from payload date fields)
- when it was retrieved (`retrieved_at`)
- how it was retrieved (`mcp`, `user`, `system`, `browser_mcp`, …)

Example:

> NVIDIA Q2 FY2027 Earnings Release  
> Source: NVIDIA Investor Relations  
> Type: PRIMARY_COMPANY / `ir_page`  
> Published: 2026-08-19  
> Retrieved: 2026-08-21  
> URL: investor.nvidia.com/…  
> Retrieval: MCP

---

## 8. Source quality

Computed, not a reliability score.

| Class | Typical `source_type` | Tier |
|---|---|---|
| PRIMARY | `filing`, `ir_page`, `transcript` | TIER_1 (transcript TIER_2) |
| SECONDARY | `news` | TIER_3 |
| MARKET | `price`, `macro`, `options` | TIER_2 / HIGH for structured data |
| DERIVED | same content, different URL | inherits group of the first copy |
| OTHER | unverified | TIER_4 |

This is **weighting and provenance**, not a claim that a source is mathematically reliable.

Taxonomy overlay (does not replace `source_type`): `SEC_FILING`, `PRIMARY_COMPANY`, `EARNINGS_TRANSCRIPT`, `MARKET_DATA`, `ECONOMIC_DATA`, `FINANCIAL_NEWS`, `OTHER`.

The important distinction remains PRIMARY vs SECONDARY vs DERIVED.

---

## 9. Source grouping

Phase 4 `independence_group` is reused.

Ten articles repeating one release share a group. They are **not** ten independent confirmations.

Same content hash + different URL → keep both, mark the later item **DERIVED**, same `source_group`.

---

## 10. Deduplication

**Hashed:** whitespace-collapsed lowercase excerpt, or title if excerpt is empty.

**Not hashed:** URL, timestamps, retrieval method, mutable metadata.

| Situation | Result |
|---|---|
| Same hash + same canonical URL | Drop as `EVIDENCE_DEDUPLICATED` |
| Same hash + different URL | Keep; mark DERIVED; same group |
| Same event, different write-up | Keep as distinct items |

Canonical URL strips `www.`, trailing slash, `utm_*`, and fragment.

---

## 11. Temporal relevance

Calendar day vs event day (Phase 4 `temporal_label`):

| Label | Meaning |
|---|---|
| PRE_EVENT | published before the event day |
| EVENT_TIME | same calendar day |
| POST_EVENT | after the event (includes retrospective >3 days) |
| UNKNOWN | missing publish time or missing event time |

**Rule:** post-event information cannot silently become evidence for what caused the event. The assessment adds a gap: “Support is post-event and cannot silently prove causation.”

---

## 12. Evidence conflicts

Deterministic, from supporting vs contradicting text — not an LLM verdict.

| Kind | When |
|---|---|
| NO_CONFLICT | no counter-items |
| TEMPORAL_DIFFERENCE | quarter / near-term vs annual / full-year language |
| DIRECT_CONTRADICTION | opposite phrases (`cut guidance` vs `guidance unchanged`, `miss` vs `beat`) |
| CONTEXTUAL_DIFFERENCE | both sides present but not a direct/temporal clash |

Quarterly vs annual guidance is **not** blindly labeled a contradiction.

---

## 13. Claim support

Interpretable status. The LLM does not get to store “confidence = 97%” as truth.

Documented model (`_support_status`):

1. No relevant support, no contradiction → `INSUFFICIENT_EVIDENCE` if gaps else `UNSUPPORTED`
2. No relevant support, has contradiction → `CONTRADICTED`
3. Direct contradiction and contra ≥ support → `CONTRADICTED`
4. Causal claim, no primary source, and gaps → `INSUFFICIENT_EVIDENCE`
5. ≥2 primary sources, ≥2 independence groups, ≤1 contra, not a direct contradiction → `STRONGLY_SUPPORTED`
6. ≥1 primary, no contra → `SUPPORTED`
7. Relevant + contra → `PARTIALLY_SUPPORTED`
8. Relevant, no primary, causal → `PARTIALLY_SUPPORTED`
9. Relevant, no primary, non-causal → `SUPPORTED`

A claim with **no evidence cannot become SUPPORTED**.

---

## 14. Evidence sufficiency

Gaps are first-class strings on the assessment, for example:

- “Missing primary regulatory / company source.”
- “Causal claim lacks a primary company or filing source.”
- “Support is post-event and cannot silently prove causation.”
- Fabricated citation reasons

`INSUFFICIENT_EVIDENCE` is exposed on the claim and in `evidence_summary` so the existing Phase 3 loop can request more research later. Phase 3 was **not** rewritten.

---

## 15. Verification integration

The existing verification agent (`investigation_verification_agent`) now receives:

- `hypothesis_context` (Phase 4 leading/alternative context)
- `claim_assessments` from `build_snapshot`

It still hard-filters unlinked claims (FR-3). It also:

- rejects fabricated URLs
- rejects “According to …” with no persisted link
- rejects invalid evidence indices
- appends notes for `INSUFFICIENT_EVIDENCE` / `UNSUPPORTED` / conflicts

There is no second verification engine.

---

## 16. Devil's Advocate integration

The existing Devil’s Advocate receives `advocate_context(snapshot)`:

- evidence summary
- conflicts
- gaps
- `cherry_pick_risk`
- `temporal_leakage`

The prompt asks it to look for cherry-picking, over-weighting weak sources, ignored contradictions, causal overreach, temporal leakage, and insufficient evidence.

Heuristic fallback adds those notes when the LLM is unavailable. No second critic agent.

---

## 17. API behavior

Existing routes are unchanged. Additive fields only.

| Route | Phase 5 |
|---|---|
| `GET /api/investigations/{id}` | `evidence_summary`, per-claim `claim_type` / `support_status` / `support_assessment`, per-evidence provenance |
| `GET /api/investigations` | same list shape |
| `POST .../evidence` | enrich + hash; identical hash+URL returns the existing row |
| `POST .../claims` | infers `claim_type` |
| `POST .../links` | `supports` / `contradicts` / `context` |

Filtering (primary, secondary, temporal, by claim) is done from the existing payload plus `evidence_graph.filter_evidence`. No extra endpoints.

`roster.evidence_graph` holds the investigation snapshot after a run.

---

## 18. Security

- LLM text is not evidence.
- Cited URLs must match a persisted `source_url` (canonicalized).
- Unknown URLs are stripped to `[citation removed]` on persist.
- “According to …” without a link is not accepted as supported.
- Invalid indices log `EVIDENCE_VALIDATION_FAILED` and are dropped.
- No fabricated URLs are invented.
- External data still goes through MCP. No new direct HTTP from investigation logic.

---

## 19. Testing

`backend/tests/test_investigation_phase5_evidence.py` covers:

- evidence create / retrieve / provenance / hash / dedup / grouping
- claim create / support / contradict / context
- temporal PRE_EVENT / EVENT_TIME / POST_EVENT / UNKNOWN
- source quality primary / secondary / low
- support statuses including the NVDA guidance example
- conflicts: direct, temporal, contextual
- gaps and missing primary source
- fabricated citation + invalid reference
- verification + Devil’s Advocate consume the graph
- Phase 3 investigate persist + Phase 4 ranking still work
- existing list/detail/link API shapes remain compatible

Structured events (no secrets, no private chain-of-thought):

`EVIDENCE_CREATED`, `EVIDENCE_DEDUPLICATED`, `EVIDENCE_LINKED`, `CLAIM_CREATED`, `CLAIM_{STATUS}`, `EVIDENCE_CONFLICT_DETECTED`, `EVIDENCE_GAP_DETECTED`, `EVIDENCE_VALIDATION_FAILED`

---

## 20. Example investigation

**Event:** NVDA falls 8.7% on 2026-08-20.

**Claim (CAUSAL_EXPLANATION):** “Guidance concerns likely contributed to the decline.”

| Role | Item | Class | Temporal |
|---|---|---|---|
| SUPPORTS | NVIDIA Q2 FY2027 Earnings Release (IR) | PRIMARY / TIER_1 | PRE_EVENT (2026-08-19) |
| SUPPORTS | Earnings transcript | PRIMARY / TIER_2 | EVENT_TIME |
| CONTRADICTS | Reuters: strong revenue / beat | SECONDARY / TIER_3 | EVENT_TIME |
| CONTEXT | Semiconductor sector decline | MARKET | EVENT_TIME |

**Assessment inputs:**

- Relevant supporting items: 2
- Primary support: 2
- Independent groups: 2 (IR host vs transcript host)
- Contradicting: 1
- Conflict: CONTEXTUAL_DIFFERENCE (guidance vs revenue are different facts)
- Temporal: EVENT_TIME / PRE_EVENT — not post-event leakage

**Result:** `STRONGLY_SUPPORTED`

That is **not** “true” and **not** a probability. It means: two independent primary/near-primary sources support the causal claim, one secondary item records a different (revenue) fact, and the system can show the chain.

**FACT** (separate claim): “Gross margin guidance declined.” → `SUPPORTED` by the IR release.

**Limitation** the report should keep visible: no management quote that *explicitly* attributes the stock print to guidance. Devil’s Advocate still sees the revenue beat as ignored or under-weighted counter-context.

---

## Configuration / schema

| Change | Detail |
|---|---|
| `evidence_items.content_hash` | `VARCHAR(64)`, added by `_migrate_evidence_graph_columns` |
| `claims.claim_type` | `VARCHAR(32)`, default `CAUSAL_EXPLANATION` |

Existing rows keep working. Assessments compute on read.

---

## What Phase 5 did not implement

- OpenClaw expansion / Browser MCP acquisition (Phase 6)
- Company memory
- Historical evaluation
- CrewAI
- Frontend redesign
- A second evidence store
- A rewrite of the Phase 3 LangGraph engine
- Fake numeric reliability of news sources
