# QuantPilot Phase 7 — Verification + Devil's Advocate Hardening

Canonical write-up: [`docs/PHASE_7_VERIFICATION_DEVILS_ADVOCATE.md`](PHASE_7_VERIFICATION_DEVILS_ADVOCATE.md).

**Scope:** Make conclusions survive an attempt to disprove them. Company memory, historical evaluation, CrewAI, frontend redesign, LangGraph replacement, OpenClaw expansion, Evidence Graph redesign, portfolio optimization, and trading execution were not implemented.

**Source of truth:** Implementation.

---

## 1. Purpose

Phases 1–6 can research a move, rank hypotheses, and attach evidence. That is not enough.

The product must **not** do:

```
Research → find a plausible explanation → generate a report
```

It now does:

```
Research
  → Hypothesis ranking
  → Evidence assessment
  → Verification (four layers)
  → Devil's Advocate (attack the lead)
  → Challenge assessment
  → more research (at most once) or final synthesis
```

A conclusion is presented as the leading explanation only after it has been verified and challenged. Causal language is hedged unless the Evidence Graph can justify it.

---

## 2. What already existed

Phase 7 extends the existing path. There is **no second verifier** and **no second critic**.

| Piece | Role kept |
|---|---|
| `investigation_verification_agent.py` | FR-3: no claim without a persisted evidence link; citation validation |
| `devils_advocate_agent.py` | Same agent; still `held` / `confidence_cut` / `demoted` |
| Phase 5 `evidence_graph` | Support status, conflicts, provenance, source groups, claim types |
| Phase 3 `investigation_graph` | Rank → verify → challenge → synthesize |
| Ledger tables | `investigations`, `claims`, `evidence_items`, `claim_evidence_links` |

What was missing, and is now added:

- Four deterministic verification layers
- Explicit FACT vs INTERPRETATION vs CAUSAL_EXPLANATION treatment
- Causal hedging when certainty is insufficient
- Source independence via Phase 5 `source_group` (reprints are not four confirmations)
- Mandatory retrospective label for post-event evidence
- Structured Devil's Advocate result (severity + recommendation)
- One bounded challenge → research loop

---

## 3. Architecture

```
collect (MCP)
    ↓
Phase 4 rank          (relative support_score, not a probability)
    ↓
Phase 5 snapshot      (claim assessments, conflicts, gaps, groups)
    ↓
verify_hypotheses     FR-3 hard filter
    ↓
verification_engine   L1 source → L2 claim → L3 hypothesis → L4 conclusion
    ↓
Devil's Advocate      attack the lead (same agent)
    ↓
challenge assessment  LOW/MEDIUM/HIGH/CRITICAL
    ↓
  HIGH/CRITICAL and unused tools and challenge_loops < MAX_CHALLENGE_LOOPS
        → identify_gaps → research → assess → rank → verify → challenge
  else
        → synthesize (supporting + opposing evidence both visible)
```

Code:

- `backend/app/services/verification_engine.py` — deterministic layers + challenge classification
- `backend/app/models/verification_schemas.py` — `VerificationReport`, `DevilsAdvocateResult`
- `backend/app/agents/investigation_verification_agent.py` — runs the engine after FR-3
- `backend/app/agents/devils_advocate_agent.py` — structured attack output
- `backend/app/workflows/investigation_graph.py` — `route_after_challenge`

---

## 4. Level 1 — Source verification

For each evidence row, check:

- content is non-empty
- retrieval did not record an error
- URL exists when the source type is not price/macro/options
- URL is a valid `http(s)` URL with a host
- official types (`filing`, `ir_page`, `transcript`) prefer an official host
- publication date exists where expected (news / IR / filing / transcript)
- retrieval timestamp exists where expected
- `source_type` is present

A missing publication date is an issue on the source check. It does **not** by itself drop a grounded claim. FR-3 still owns keep/reject.

A URL is not treated as proof. Primary sources still receive stronger evidentiary treatment via Phase 5 class/tier.

---

## 5. Level 2 — Claim / evidence verification

For each ranked hypothesis:

- the statement is actually linked to persisted evidence
- citations map to real rows (fabricated URLs still fail)
- evidence is relevant (Phase 5 `relevance_for_claim`)
- evidence is temporally appropriate
- the recorded relationship (supports / contradicts / context) is respected
- contradictions already in the ledger are surfaced, not hidden
- additional weakeners in the pool are searched for (“what would make this weaker?”)

Post-event support is labeled **retrospective**. It must not silently prove what caused the original event.

---

## 6. Level 3 — Hypothesis verification

The leading hypothesis must:

- have sufficient supporting evidence (`PARTIALLY_SUPPORTED` or better, with links)
- have contradictions considered if they exist
- have a strongest alternative ranked when one exists
- record known gaps

Independent support counts **source groups**, not raw row counts. Four news reprints of the same company release are one group after Phase 5 `dedupe_items` / `source_group`.

---

## 7. Level 4 — Conclusion verification

The final statement must:

- match the evidence that survived L1–L3
- use causal language only when justified
- keep uncertainty visible
- weaken or rewrite unsupported causal claims

Weak:

> NVDA fell because of guidance.

Preferred when causal certainty is insufficient:

> Guidance concerns are the strongest supported explanation identified by the investigation, although broader semiconductor weakness also remains plausible.

---

## 8. Fact vs interpretation vs causal claim

Reuses Phase 5 `infer_claim_type`. The verifier does **not** treat these as interchangeable.

| Type | Example | Evidentiary bar |
|---|---|---|
| FACT | “NVIDIA reported revenue of X.” | The source must actually state X |
| INTERPRETATION | “Investors may have viewed the margin outlook negatively.” | Relevant, temporally sound context |
| CAUSAL_EXPLANATION | “Margin guidance caused the stock to decline.” | Temporal order + mechanism + event relevance + alternatives + strength |

“Company reported weaker guidance” is a **fact**. It is not proof that “guidance caused the decline.”

---

## 9. Causal claim guardrails

Causal claims require evidence addressing:

1. Temporal ordering (pre-event / event-time vs retrospective)
2. Plausible mechanism (guidance, earnings, regulatory, …)
3. Event relevance
4. Alternative explanations
5. Evidence strength (primary sources, independent groups)

If the claim type is `CAUSAL_EXPLANATION` and support is not `STRONGLY_SUPPORTED`, the statement is rewritten to the “strongest supported explanation … although …” form. Facts are not rewritten.

---

## 10. Source validation and independence

Uses Phase 5 / Phase 6 metadata already on the item:

- source quality / class / type
- domain and URL
- published_at / retrieved_at
- temporal_relevance
- source_group

Primary sources weigh more. A URL is necessary for most non-market items and is never sufficient by itself.

Independence:

```
Company earnings release
    quoted by Reuters, CNBC, MarketWatch, Yahoo Finance
```

These are **not** four independent confirmations. Same content hash shares a `source_group`. The verifier counts groups.

---

## 11. Temporal validation

For an event on **2026-08-20**:

| Evidence date | Label | May prove the original cause? |
|---|---|---|
| 2026-08-19 | PRE_EVENT | Yes, as prior fact |
| 2026-08-20 | EVENT_TIME | Yes, as contemporaneous |
| 2026-08-25 | POST_EVENT / retrospective | No, not silently |

Post-event items may still appear as context. Verification notes must say they are retrospective.

---

## 12. Contradiction detection

For every important claim the engine asks: **what existing evidence would make this weaker?**

Example — “Guidance concerns caused the decline.”

Supporting:

- guidance reduced
- management commentary weakened

Contradicting / weakening (must remain visible):

- annual guidance unchanged
- revenue exceeded expectations
- semiconductor sector declined with the name

Conflicts are copied into the verification report and into Devil's Advocate `contradictory_evidence`.

---

## 13. Devil's Advocate

Same agent. Structured context now includes:

- event
- leading hypothesis and strongest alternative
- supporting and contradicting evidence
- evidence gaps
- source quality
- temporal information
- market context (move / residual)
- verification report

It must attack the lead and answer:

1. Strongest argument against the leading explanation
2. Contradicting evidence
3. Assumptions being made
4. Over-weighted evidence
5. Ignored evidence
6. Whether the strongest alternative is more plausible
7. What additional evidence would change the conclusion

---

## 14. Structured Devil's Advocate output

`DevilsAdvocateResult`:

| Field | Meaning |
|---|---|
| `target_hypothesis_id` | Lead under attack |
| `strongest_counterargument` | Best case against the lead |
| `contradictory_evidence[]` | Visible counter-items |
| `ignored_evidence[]` | Items that may have been under-weighted |
| `questionable_assumptions[]` | e.g. fact treated as cause |
| `alternative_hypothesis_id` | Strongest competitor |
| `alternative_strength` | Qualitative only |
| `additional_evidence_needed[]` | Mapped onto existing MCP tools if a loop runs |
| `challenge_severity` | LOW / MEDIUM / HIGH / CRITICAL |
| `recommendation` | ACCEPT / REVIEW / RESEARCH_MORE / REJECT |

Legacy fields (`outcome`, `counterargument`, `leading_weakened`, `reversal`) remain. Additive API only.

---

## 15. Challenge severity

| Severity | Typical meaning | Default recommendation |
|---|---|---|
| LOW | Minor uncertainty; lead held | ACCEPT |
| MEDIUM | Some contradiction, temporal leakage, or confidence cut | REVIEW |
| HIGH | Strong alternative, cherry-pick + conflict, or causal claim with no primary | RESEARCH_MORE if unused tools else REVIEW |
| CRITICAL | Lead unsupported / insufficient / no reliable evidence | RESEARCH_MORE or REJECT |

HIGH or CRITICAL can request one more research pass.

---

## 16. LangGraph challenge loop

```
rank → verify → challenge
                 │
                 ├─ ACCEPT / REVIEW / no unused tools → synthesize
                 └─ RESEARCH_MORE / REJECT / HIGH / CRITICAL
                    and unused mapped tools
                    and challenge_loops < MAX_CHALLENGE_LOOPS (default 1)
                         → identify_gaps → research → assess → rank → verify → challenge
```

Mapped needs:

| Need text | Tool |
|---|---|
| filing / 8-K / SEC | `get_recent_filings` |
| IR / transcript / primary | `get_ir_materials` |
| presentation / PDF | `browser_find_document` (still budgeted) |
| news | `get_stock_news` |
| SPY / sector / price | `get_price_history` |

Tools already used, or already satisfied by ledger rows, are not re-requested.

Mocked Phase 3 Devil's Advocate results that omit `recommendation` / `challenge_severity` default to **ACCEPT** and do **not** open a second research loop.

---

## 17. Final synthesis

`_finalize_summary` now states:

- the move
- the leading (possibly hedged) explanation
- claim type
- strongest alternative
- Devil's Advocate outcome, severity, recommendation
- the opposing case

The Evidence Ledger still shows supporting and contradicting links. The report must not hide the challenge.

---

## 18. Observability

Structured events (no secrets, no private chain-of-thought):

`VERIFICATION_LAYER_COMPLETED`, `CAUSAL_CLAIM_HEDGED`, `VERIFICATION_STARTED`, `DEVILS_ADVOCATE_STARTED`, `ADDITIONAL_RESEARCH_REQUIRED`, `INVESTIGATION_SYNTHESIS`

---

## 19. Testing

`backend/tests/test_investigation_phase7_verification.py` covers:

- L1 source checks (URL, domain, empty content, dates)
- L2 claim/evidence support and citation mapping
- L3 leading sufficiency, alternative, gaps
- L4 conclusion / causal hedge
- FACT vs INTERPRETATION vs CAUSAL
- temporal retrospective labeling (2026-08-20 event, 2026-08-25 evidence)
- independence / source grouping (reprints are not four confirmations)
- contradictions surfaced
- structured Devil's Advocate + severity
- HIGH/CRITICAL can request more research; ACCEPT does not loop
- Phase 3 / 4 / 5 / 6 paths still work
- existing investigation API shapes remain compatible

---

## 20. Example investigation

**Event:** NVDA falls 8.7% on 2026-08-20.

**Leading hypothesis (raw):** “Guidance concerns caused the decline.”

**Verification**

- FACT present: gross-margin guidance declined (IR, 2026-08-19, PRE_EVENT, PRIMARY)
- FACT present: revenue exceeded estimates (Reuters, EVENT_TIME, SECONDARY)
- CONTEXT: semiconductor sector declined with the name
- Causal status is not `STRONGLY_SUPPORTED` once the revenue beat and sector tape are counted
- Statement is rewritten to the strongest-supported-explanation form
- Retrospective 2026-08-25 recap, if present, is labeled post-event and cannot prove the 20 August print

**Devil's Advocate**

- Strongest counter: market-wide semiconductor weakness and a revenue beat undercut a pure guidance story
- Assumption challenged: “weaker guidance was reported” ≠ “guidance caused the drop”
- Recommendation: REVIEW or RESEARCH_MORE (official transcript / presentation) if those tools are still unused
- Severity: MEDIUM or HIGH depending on primary support and the alternative

**Final line (shape):**

> NVDA fell 8.7% over 1 day. Guidance concerns is the strongest supported explanation identified by the investigation, although a broader semiconductor decline also remains plausible. Devil's Advocate did not treat the lead as proven. Opposing case: revenue beat and sector weakness.

That is a **supported** explanation, not a proven cause.

---

## Configuration / schema

| Change | Detail |
|---|---|
| `MAX_CHALLENGE_LOOPS` | Default `1` |
| `DevilsAdvocateOutcome` | Additive: `challenge_severity`, `recommendation`, `alternative_hypothesis_id`, `additional_evidence_needed`, `questionable_assumptions` |
| `roster.investigation_graph` | Additive: `verification_report`, `challenge_loops` |
| `da_outcome_json` | Same column; extra keys |

No new tables. No new agents.

---

## What Phase 7 did not implement

- Company memory
- Historical evaluation
- CrewAI
- Frontend redesign
- LangGraph replacement
- OpenClaw expansion
- Evidence Graph redesign
- Autonomous portfolio optimization
- Trading / brokerage execution
- A second verification system or a second critic agent
