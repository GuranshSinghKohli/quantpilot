# QuantPilot Phase 7 — Verification & Devil's Advocate

**Scope:** Harden conclusions so they survive an attempt to disprove them. “I don’t know” is a valid outcome. Company memory, historical evaluation, CrewAI, frontend redesign, LangGraph replacement, OpenClaw expansion, Evidence Graph redesign, portfolio optimization, and trading execution were **not** implemented.

**Source of truth:** Implementation.

Related: `docs/PHASE_7_VERIFICATION.md` (implementation notes from the first Phase 7 pass).

---

## 1. Purpose

The product loop is:

```
Detect → Plan → Research → Hypotheses → Evidence
  → Verify → Attack the conclusion → Research again if necessary → Conclude
```

The system must **not** force a polished causal story. Insufficient evidence is a feature.

This is an explanation product: *what happened, and what does the evidence support?*  
It is not a trading advisor. Verification never emits BUY / SELL / HOLD.

---

## 2. Verification architecture

Phase 7 **extends** the existing verifier and the existing Devil’s Advocate. There is no second orchestration system.

```
MCP collect
    ↓
Phase 4 rank                 relative support_score (internal, not a probability)
    ↓
Phase 5 Evidence Graph       support, conflicts, groups, claim types
    ↓
verify_hypotheses            FR-3 + citation filter
    ↓
verification_engine          L1–L4 + hallucination pass + conclusion strength
    ↓
Devil's Advocate             same agent; structured attack
    ↓
challenge assessment
    ↓
  research-more conditions AND unused MCP tools
  AND challenge_loops < MAX_CHALLENGE_LOOPS
  AND iteration_count < MAX_INVESTIGATION_ITERATIONS
        → identify_gaps → research → assess → rank → verify → challenge
  else
        → synthesize (FinalInvestigationReport)
```

Code:

| File | Role |
|---|---|
| `backend/app/services/verification_engine.py` | Deterministic layers, gaps, final report |
| `backend/app/models/verification_schemas.py` | Report / DA / conclusion types |
| `backend/app/agents/investigation_verification_agent.py` | Existing verifier, now runs the engine |
| `backend/app/agents/devils_advocate_agent.py` | Existing critic, structured output |
| `backend/app/workflows/investigation_graph.py` | `route_after_challenge`, synthesis |

---

## 3. Verification layers

**L1 Source** — exists, URL/domain, type, published/retrieved timestamps where expected, non-empty content. A URL is not proof.

**L2 Claim / evidence** — the claim is actually supported, relevant, temporally sound, and the citation maps to a persisted row.

**L3 Hypothesis** — the lead has enough support; the strongest alternative and contradictions were considered; gaps are recorded.

**L4 Conclusion** — the closing statement matches the evidence; causal language is justified or softened; uncertainty stays visible.

Then a **final hallucination / citation pass** (`finalize_claims`) before synthesis.

---

## 4. Fact vs interpretation vs causal claim

Reuses Phase 5 `infer_claim_type`. These are not interchangeable.

| Type | Example | Bar |
|---|---|---|
| FACT | “NVIDIA reported revenue of X.” | The source must state X |
| INTERPRETATION | “Investors may have viewed the margin outlook negatively.” | Relevant, timely context |
| CAUSAL_EXPLANATION | “Margin guidance caused the decline.” | Temporal order + mechanism + alternatives + strength |

“Company reported weaker guidance” is a **fact**. It is not proof that guidance **caused** the print.

---

## 5. Source validation

Uses Phase 5 / Phase 6 metadata already on the item: quality, class, type, domain, timestamps, `source_group`, temporal label.

Primary sources weigh more. Official hosts are required for `filing` / `ir_page` / `transcript`. Price/macro items may omit a URL.

Independence: four news reprints of the same release share a `source_group`. They count as one confirmation.

---

## 6. Temporal validation

Event date **2026-08-20**:

| Evidence date | Label | May prove the original cause? |
|---|---|---|
| 2026-08-19 | PRE_EVENT | Yes, as prior fact |
| 2026-08-20 | EVENT_TIME | Yes, contemporaneous |
| 2026-08-25 | POST_EVENT / retrospective | No, not silently |

Retrospective evidence must be labeled. It cannot silently prove causation.

---

## 7. Contradiction detection

For important claims the engine asks what existing evidence would make the claim weaker, then inspects the pool.

Conflicts stay visible in:

- verification notes / `claim_checks.contradictions`
- Devil’s Advocate `contradictory_evidence`
- `FinalInvestigationReport.contradicting_evidence`

Kinds (Phase 5): `DIRECT_CONTRADICTION`, `CONTEXTUAL_DIFFERENCE`, `TEMPORAL_DIFFERENCE`.

---

## 8. Devil's Advocate

Same agent. Structured context: event, lead, strongest alternative, supporting/contradicting evidence, gaps, source quality, temporal labels, market context, verification report.

It must attack the lead and answer:

1. Strongest argument against the lead  
2. Contradicting evidence  
3. Assumptions  
4. Over-weighted evidence  
5. Ignored evidence  
6. Whether the alternative is more plausible  
7. What extra evidence would change the call  

Output: `DevilsAdvocateResult` (`challenge_severity`, `recommendation`, lists — not free prose only).

---

## 9. Challenge severity

| Severity | Meaning | Typical recommendation |
|---|---|---|
| LOW | Minor uncertainty | ACCEPT |
| MEDIUM | Some contradiction or confidence cut | REVIEW |
| HIGH | Strong alternative, cherry-pick, or causal with no primary | RESEARCH_MORE or REVIEW |
| CRITICAL | Lead unsupported / no reliable evidence | RESEARCH_MORE or REJECT |

---

## 10. More-research conditions

Additional research is requested when **unused MCP tools remain** and at least one of:

- leading hypothesis lacks primary evidence
- contradiction is strong
- evidence is insufficient
- causal claim is unsupported
- strongest alternative is close to the lead
- source quality is weak
- temporal validity is questionable

or Devil’s Advocate returns `RESEARCH_MORE` / `REJECT` / HIGH / CRITICAL.

**Do not research indefinitely.**

- `MAX_CHALLENGE_LOOPS` default **1**
- `MAX_INVESTIGATION_ITERATIONS` (Phase 3) is respected: if `iteration_count >= max_iterations`, the challenge loop does not fire

Mocked Phase 3 Devil’s Advocate results that omit severity/recommendation still default to ACCEPT and do not invent a loop.

---

## 11. Conclusion strength

Structured assessment. The system does **not** force a cause.

| Strength | Meaning |
|---|---|
| ROBUST | Strong primary support, low challenge |
| SUPPORTED | Supported, with qualifications |
| PARTIALLY_SUPPORTED | Mixed or retrospective support |
| UNCERTAIN | High challenge or weak mix |
| INSUFFICIENT_EVIDENCE | Not enough to name a primary cause |
| CONTRADICTED | Lead is contradicted |

Valid closing line:

> Insufficient evidence to determine the primary cause.

Confidence language for humans: **HIGH / MEDIUM / LOW** (or the strength labels above).

Internal `support_score` remains a **ranking metric**. It is not presented as a calibrated probability. Never “87.43% confidence.”

---

## 12. Causal language controls

| Evidence | Form |
|---|---|
| Incomplete / not strongly supported | “Guidance concerns appear to be the strongest supported explanation, but the evidence does not establish causality with certainty.” |
| Strong (primary + contemporaneous) | “Guidance concerns were a significant contributor to the decline, based on the company's release and contemporaneous market reaction.” |
| Insufficient | “Insufficient evidence to determine the primary cause.” |

Do not write “NVDA dropped because of guidance” unless the bar is actually met — and even then prefer “significant contributor,” not certainty.

---

## 13. Hallucination defense

Before the final report, every statement is checked:

- factual claims must map to retrieved evidence
- “Management said …” / quoted speech must appear in an evidence excerpt
- causal claims need support + temporal relevance + an alternative assessment
- missing or invalid `evidence_indices` → reject

If there is no evidence containing the statement: **REJECT CLAIM**. Do not invent a replacement.

---

## 14. Citation validation

Every citation must map to a persisted evidence URL or index. Reject:

- fake URL
- fabricated source
- missing evidence
- invalid evidence ID

The system does **not** auto-invent a replacement citation. Phase 5 still strips unknown URLs to `[citation removed]` on persist.

---

## 15. Evidence gap reporting

When the investigation cannot establish a strong conclusion, gaps are listed on the final result, for example:

- No official management commentary available.
- No primary-source regulatory announcement found.
- Only secondary news sources available.

Stored on `roster.evidence_gaps` and `FinalInvestigationReport.evidence_gaps`.

---

## 16. Verification summary

```
Claims reviewed: N
Supported / Partially supported / Unsupported / Contradicted
Primary-source coverage: HIGH | MEDIUM | LOW
Major contradiction: …
Devil's Advocate: MEDIUM challenge
Final conclusion: SUPPORTED WITH QUALIFICATIONS
```

---

## 17. Final investigation format (data contract)

Backend fields (no frontend redesign):

| Section | Field |
|---|---|
| WHAT HAPPENED | `what_happened` |
| WHY IT LIKELY HAPPENED | `why_it_likely_happened` |
| STRONGEST ALTERNATIVE | `strongest_alternative` |
| SUPPORTING EVIDENCE | `supporting_evidence[]` |
| CONTRADICTING EVIDENCE | `contradicting_evidence[]` |
| VERIFICATION | `verification` |
| DEVIL'S ADVOCATE | `devils_advocate` |
| EVIDENCE GAPS | `evidence_gaps[]` |
| PORTFOLIO IMPACT | `portfolio_impact` (informational; not a trade) |
| FINAL ASSESSMENT | `final_assessment` |
| SOURCES | `sources[]` |

Attached additively on `roster.final_report` and `InvestigationGraphResult.final_report`.

---

## 18. LangGraph integration

`rank → verify → challenge → (identify_gaps → research → …) | synthesize`

OpenClaw is **not** expanded. Browser MCP rows (`retrieval_method=browser_mcp`) are ordinary evidence for the verifier.

MCP remains the only external I/O path from investigation logic.

---

## 19. Testing

`backend/tests/test_investigation_phase7_verification.py` covers source, claim, causal, temporal, independence, contradictions, Devil’s Advocate, research-more, max-iteration cap, hallucination/citation rejection, evidence gaps, final report, Phase 3–6 compatibility, and a mocked NVDA end-to-end run.

Observability (no secrets, no private chain-of-thought):

`VERIFICATION_STARTED`, `CLAIM_VERIFICATION_FAILED`, `CLAIM_VERIFIED`, `CONTRADICTION_DETECTED`, `CAUSALITY_CHALLENGED`, `DEVILS_ADVOCATE_STARTED`, `DEVILS_ADVOCATE_COMPLETED`, `MORE_RESEARCH_REQUIRED`, `CONCLUSION_DOWNGRADED`, `CONCLUSION_ACCEPTED`, `UNSUPPORTED_CLAIM_REJECTED`

Fields: `investigation_id`, `claim_id` / `hypothesis_id`, `challenge_severity`, `verification_status`.

---

## 20. Example investigation

**EVENT:** NVDA −8.7% on 2026-08-20.

**LEADING HYPOTHESIS:** Guidance concerns.

**STRONGEST ALTERNATIVE:** Market / sector selloff.

**Supporting**

- NVIDIA IR earnings release (2026-08-19, PRE_EVENT, PRIMARY): gross-margin guidance declined.
- Contemporaneous tape: the name sold off with the print.

**Contradicting**

- Reuters: revenue beat estimates (EVENT_TIME).
- Annual guidance unchanged (contextual / temporal difference).
- Semiconductor sector declined with NVDA.

**Devil’s Advocate**

- Strongest counter: a revenue beat plus sector weakness undercut a pure guidance story.
- Assumption challenged: “weaker guidance was reported” ≠ “guidance caused the drop.”
- Severity: MEDIUM. Recommendation: REVIEW.

**Verification**

- FACT “guidance declined” is supported by the IR release.
- CAUSAL “guidance caused the decline” is **not** strongly supported.
- Citations map to persisted rows. No fabricated URLs.

**Final conclusion**

> NVDA fell 8.7% over 1 day. Guidance concerns appear to be the strongest supported explanation, but the evidence does not establish causality with certainty. Broader semiconductor weakness also remains plausible. Conclusion strength: PARTIALLY_SUPPORTED or SUPPORTED WITH QUALIFICATIONS. Confidence: MEDIUM (not a calibrated probability). Evidence gaps listed if no transcript was retrieved.

That is a **supported explanation with qualifications**, not a proven cause, and not a trade recommendation.

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
- A second verifier or second critic
