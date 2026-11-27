# Phase 13 — deterministic claim and market fact grounding

The Phase 12 audit is a 33-claim manual review of real staging reports. In that review, 14 claims were factually correct and fully supported. QuantPilot marked 26 supported, and 12 of those 26 met the fully-supported bar. Seven supported claims were factually incorrect. Two of seven sampled market claims matched both the security and the stated percent. The audit predates the deployed ticker-binding fix, so it does not measure that fix.

This phase makes a market observation come from one stored impact window, and it tightens which document sentences can stay supported. It does not add brokers, databases, agents, or event-study math. Replaying the audit file does not rewrite the old reports.

## Phase 12 failure taxonomy

| Class | What the report did | Audit cases |
| --- | --- | --- |
| Wrong security | A Microsoft question carried an NVDA citation, or an NVDA citation carried another security’s fraction | hca-11, hca-33, hca-28 |
| Wrong unit | A stored fraction such as `0.0134` was printed as `0.0134%` | hca-29, and the percent in hca-28 |
| Mixed window | The asset return came from one event and the benchmark from another | hca-31 |
| Entity insertion | The sentence named a company the cited excerpt does not contain | hca-16, hca-17 |
| Scope overclaim | The sentence widened the subject, population, or attribution of a real source | hca-05, hca-06, hca-09, hca-15, hca-22, hca-23, hca-26, hca-30 |
| Incomplete context | A true fragment was presented without the source’s limit | hca-04, hca-08 |
| Investment-language reject | A sourced rating was excluded by the existing buy/sell/hold rule | hca-14 |
| Missing relationship row | An explicit partnership sentence did not become a relationship candidate | two source-supported pairs in the audit |

## Root causes

Market numbers were model text. The specialist could turn a stored fraction into a percent, attach a percent sign to the fraction, or describe one window with numbers from another row. Phase 11 already drops an impact whose entity and symbol are not the resolved security. The model could still rewrite a number after a matching citation existed.

A document claim could stay supported because the article was about the same topic. The verifier did not compare the sentence’s subject, population, or time scope with the excerpt.

Relationship candidates required a narrow cue list. “Partnership” matched. “Strategic relationships” did not. Co-mention without a cue was already rejected, and that rule stays.

## Changes

Market observations are rendered in `backend/app/market/render.py`. `format_return_percent` does `round(stored_fraction * 100, 2)` and writes `+1.59%` for a positive value. One statement is one window:

`Microsoft (MSFT) returned +1.59% over the +1 session window, versus +1.55% for SPY.`

The symbol, entity, event, benchmark, calculation version, asset return, benchmark return, and relative return are copied from the impact row. The model does not supply them.

`ground_market_findings` drops a market FACT or OBSERVATION, and any market finding that contains a percent or a bare decimal. It then prepends the rendered observations. The verifier accepts an OBSERVATION only when the statement equals one rendered window and the impact, citation, and resolved security share entity, symbol, and event. A mismatch is `entity_mismatch`, `symbol_mismatch`, `event_mismatch`, or `security_mismatch`, and the claim is unsupported.

Document FACTs and INFERENCEs run `document_support_issues`. A package entity named in the sentence and absent from the excerpt is `entity_not_in_source`. For a shared percent or dollar figure, the nearest capitalized phrase before the number must overlap, or the claim is `scope_changed`. Plural “analysts” against a singular analyst in the excerpt is `population_widened`. A quarter/year/week/month that appears on only one side is a period mismatch. A hedged excerpt with an unhedged claim is `certainty_raised`. SCENARIO text still needs conditional wording. Buy/sell/hold handling is unchanged.

Prompt versions in use are `market-v4` and `evidence-v3`. The evidence agent still does not call a model. The version marks the verifier change.

`PARTNERED_WITH` cues now include “relationship”. A cue preceded by no/not/never/without is not treated as affirmed. Two companies named in the same sentence still do not create a candidate unless a cue is affirmed.

Verified claims carry `claim_id`, `source_ids`, `evidence_ids`, `entity_id`, and `provenance` on the report item. `claim_id` remains `sha256(finding_id|status)[:16]`. There is no new `research_claims` column. Evidence links are written when `evidence_id` is a real evidence-row UUID.

Retrieval imports security resolution inside `assemble_package`. A module-level import initialized the research package while that package was still loading retrieval, and the API process exited on startup.

## Before and after

| Situation | Before | After |
| --- | --- | --- |
| Microsoft question, MSFT `0.015857…` and an NVDA `0.02299…` row on the same event | The sentence could say 1.59% while the citation symbol was NVDA | The Microsoft package loads the MSFT row. The observation says `+1.59%` versus `+1.55%` for SPY. An NVDA row is not substituted |
| Model writes `0.0159` or `0.0159%` | That string could be the supported observation | Numeric market findings are dropped. The renderer writes `+1.59%`. A leftover raw-fraction observation is unsupported |
| `+1.34%` from one NVDA event paired with `+1.55%` from another | One sentence could mix the windows | The statement must equal exactly one stored window |
| Excerpt names Alphabet and Apple; sentence names Microsoft | Topic overlap could support the sentence | `entity_not_in_source` |
| “Intelligent Cloud revenue grew 32%” vs “Azure grew 32%” | Often left supported | `scope_changed` on the fixture |
| “One analyst has a $515 price target” vs “Analysts have a $515 price target” | Often left supported | `population_widened` on the fixture |
| “Apple proposed a partnership with Nvidia” | Cue list could miss the sentence | Candidate with both entities, `PARTNERED_WITH`, source, evidence, and provenance kind |
| “Microsoft and NVIDIA discussed AI infrastructure” | Already rejected | Still rejected |

## Audit dispositions

`eval/human_claim_audit_v1.json` labels are unchanged. `eval/phase13_grounding_results.json` records the disposition of each case under the new checks. A disposition says what the new checks do with that failure class. It does not mean the stored Phase 12 report was regenerated.

| Disposition | Cases |
| --- | --- |
| PREVENTS | hca-11, hca-28, hca-29, hca-31, hca-33 |
| DETECTS | hca-09, hca-16, hca-17, hca-26 |
| STILL ALLOWS | hca-04, hca-05, hca-06, hca-08, hca-14, hca-15, hca-22, hca-23, hca-30 |
| NOT_A_FAILURE | the claims the audit did not tag with an error |

PREVENTS means a wrong-security, wrong-unit, or mixed-window market sentence cannot become a supported market observation. DETECTS means the verifier marks the fixture unsupported. STILL ALLOWS means a supported claim can still carry that wording. hca-14 stays allowed as a reject: the investment-language rule still removes buy/sell/hold even when the source contains it.

## Tests

`backend/tests/unit/research/test_claim_grounding.py` covers wrong security, wrong unit, mixed windows, entity insertion, scope and population overclaims, deterministic rendering (`0.015857…` → `+1.59%`), citation identity, entity/symbol/event mismatch, an explicit partnership candidate, and co-mention / “discussed AI infrastructure” rejection. The same file reproduces hca-11, hca-28, hca-29, and hca-31.

Local runs in this phase:

- Research, retrieval, market, entity resolution, ticker binding, relationship candidates, and identity: 206 passed, before the import-cycle fix.
- After that fix: claim grounding, ticker binding, and retrieval: 47 passed.
- Frontend `lib/researchDesk.test.ts`: 7 passed. `tsc --noEmit` and `next lint` were clean. Frontend code was not changed, and the frontend image was not rebuilt.

## Staging

Account `225201316318`, cluster `quantpilot-staging`, image digest `sha256:73261c13ed3c587a7cc2fc0c7875c545c0f7c33e497b7a63eb73859a859c2b55`.

API service `api` is task definition `quantpilot-staging-api:8` (`phase13b-api`). Worker service `research-worker` is `quantpilot-staging-worker:13` (`phase13b-worker`). Both rollouts completed with one running task. The frontend service was left on the Phase 10 image.

The first image tag, `phase13-api`, could not boot. Retrieval imported the research package while that package was still initializing. The rebuilt image uses a function-local import. ECR tags are immutable, so the bootable image is `phase13b-api` / `phase13b-worker`.

New tasks then failed Postgres authentication. RDS recorded “Reset master credentials” for `quantpilot-staging` on 2026-10-06. The application `DATABASE_URL` secret still held the previous password, so existing tasks kept working and new tasks were rejected. The secret was updated from the current RDS-managed password. Host, user, database, and query string were preserved. After that, the Phase 13 tasks stayed up and `/health` reported `database: ok`.

The matrix used real `gpt-4o-mini` jobs. Compact results are in `eval/phase13_staging_matrix.json`.

| Job | Resolved | Event | Window | Generated | Stored | Match |
| --- | --- | --- | --- | --- | --- | --- |
| Microsoft `a13e7c68-5931-4c60-bcec-aa302794b84d` | MSFT `a6d84c30-9814-454f-99a3-66e1cdeaf237` | `031156cd-bf79-422d-98f2-7f5aa949ecb6` | +1D | +1.59% vs SPY +1.55% | +1.59% vs +1.55% | yes |
| NVIDIA follow-up `574d7ded-3cbb-4a7b-b8cb-ae897617f427` | NVDA `3462ac4e-98c0-417a-aa56-538b1d91e605` | same event | +1D | +2.30% vs SPY +1.55% | +2.30% vs +1.55% | yes |
| Meta `b0b428cb-611c-4eac-b2fa-53234adad18a` | META `6026fafe-7a6a-4e79-aec3-efee09708ff6` | `0efae0a6-0c59-4960-b9f0-43d4672c2e70` | +1D | +1.34% vs SPY +1.13% | +1.34% vs +1.13% | yes |
| Apple `53e422bc-53fe-4b15-a914-0c213b71ee98` | AAPL `2c509dc4-bf12-4c8f-ae08-4b96025cfe51` | `031156cd-bf79-422d-98f2-7f5aa949ecb6` | +1D | +0.85% vs SPY +1.55% | +0.85% vs +1.55% | yes |

Each of those four jobs produced 12 market observations. Every generated asset percent and benchmark percent matched the stored fraction for that same entity, symbol, event, and window. The Microsoft observations are all MSFT. The NVIDIA follow-up observations are all NVDA. On the shared event, Microsoft is `+1.59%` and NVIDIA is `+2.30%`.

The Microsoft job also contains an unsupported OBSERVATION whose text is the raw fraction `-0.007975891511803113`. That sentence is not in `market_observations`. The rendered window for the same stored move is `-0.80%`.

The first NVIDIA question, `3c3dcaa3-84af-4c1e-830f-a5f13301171e`, resolved NVDA and then the planner selected only the news agent. The market agent was skipped, so that job has no market observation to compare. The follow-up above is the market measurement.

The company-fact job `b807cfe9-a259-4f99-90f7-ad7b5d397eb8` (“What does Apple do?”) succeeded. Its limitation list includes the existing investment-language exclusion. The relationship job `e5c62644-3495-4156-8d85-2ac277b60894` succeeded with two company findings that cite source ids: an Apple–Nvidia partnership sentence and an AMD sentence naming OpenAI, Meta, and Anthropic. Those report rows are findings. The candidate-row fields are covered by the unit tests, not by a new relationship store.

Market prompt `market-v4` and evidence prompt `evidence-v3` are on the staging agent runs. Identity for a market citation is on `citation.market` (`entity_id`, `symbol`, `event_id`) and on the observation `provenance`. The citation object itself still has no top-level `entity_id`.

## What is still open

The comparator catches the fixture overclaims it was written for. Other audit overclaims stay possible, including causal “driven by,” declared-versus-paid, and valuation wording. On the live Microsoft job, “Azure growing 32% YoY” remained a supported FACT, and the document key facts had empty `evidence_ids`.

The planner can skip the market agent even when the question is about performance, as the first NVIDIA job did. A skipped market agent produces no rendered observation.

`claim_id` is not globally unique. Co-mention still does not create a relationship. A sourced buy/sell/hold sentence is still excluded. The 33-claim dispositions are not a new accuracy rate, and the old staging reports are unchanged.
