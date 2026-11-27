# Phase 14 — evidence integrity and research completeness

Phase 13 is unchanged. Market observations are still rendered from one stored impact window, and the model still does not choose the percent. This phase does two smaller things: a document claim cannot stay supported without a real evidence record, and an explicit market-reaction question cannot be planned without the market specialist.

It does not add a database, an agent, a broker, or an event-study engine. Phase 12 audit labels in `eval/human_claim_audit_v1.json` are unchanged. Historical reports were not rewritten. Supported here means the verifier accepted the claim. It does not mean the sentence is factually perfect.

## 1. Evidence-integrity problem

A document sentence could be marked supported because the model attached a citation. The citation was built with `evidence_id` left empty, including when no `EvidenceRecord` existed for that article. The verifier treated a resolved citation as enough. An evidence id was not required, so a FACT could be supported with `evidence_ids: []`.

The same gap applied when the model labeled the sentence OBSERVATION instead of FACT. The first document-evidence check covered FACT, INFERENCE, and SCENARIO only.

## 2. Root cause

`_cite_document` did not look up an evidence row. News ingestion stores a source and a retrieval document. It does not call `add_evidence`, so many live articles have no `EvidenceRecord`. The verifier then accepted the citation id as support.

A second, separate gap was in the planner. `accept_plan` kept whatever specialists the model returned. On the Phase 13 NVIDIA performance question the model returned only `news`, and the market specialist was skipped. Nothing after the plan checked that the question had asked for a market reaction.

## 3. Changes

Document citations now look up an existing evidence row in `backend/app/retrieval/package.py` (`_bind_document_evidence`, `_matching_evidence`). A row matches only when its source is the citation’s source, its document id agrees when one is stored, and its excerpt is contained in the chunk title and excerpt. The lookup prefers a row that has a document id, then the longer excerpt. It does not insert a row and it does not copy an arbitrary same-source row onto the citation.

`backend/app/research/verify.py` rejects a document-backed FACT, OBSERVATION, INFERENCE, or SCENARIO when that chain fails. Blocking issue names, all mapped to the existing status `unsupported`:

- `missing_evidence` — the citation has no evidence id
- `unresolved_evidence` — the id is not on the citation’s evidence snapshot
- `evidence_source_mismatch` — `evidence.source_id` is not `citation.source_id`
- `evidence_document_mismatch` — both document ids are set and they differ
- `evidence_entity_mismatch` — the claim’s `provenance.entity_id` is set, the evidence row has entity ids, and the claim entity is not among them

A citation id is not an evidence id. Market observations do not use this path. They still have to equal one rendered window, with entity, symbol, and event shared by the impact row, the citation, and the resolved security.

The evidence prompt version is `evidence-v5`. The master prompt is still `master-v4`. The market prompt is still `market-v4`.

Planner completeness is in `backend/app/research/plan.py`. `market_required` is a small deterministic check. `required_domains` is stored on the plan. After `accept_plan`, `create_research_plan_node` treats a missing required specialist as `invalid_plan`, retries once with the hint “This query requires the market specialist. Include market in selected_agents.”, and then uses `fallback_plan`. The fallback router also treats `market_required` as a market request, so the fallback includes `market`. The check does not insert `market` into a plan the model already accepted, and it does not remove `market` when the question did not require it.

## 4. Empty-evidence behavior

If a document citation has no matching evidence row, `evidence_id` stays null. The claim is `unsupported`. It is excluded from factual report sections. No id is minted, and a citation id is not written into `evidence_ids`.

INFERENCE and SCENARIO with a broken document chain are `unsupported` as well, because the blocking issues are applied before the uncertain branch. A graph-cited inference that has a real evidence id and no document citation stays `uncertain`.

A structured listing is not a document excerpt. “Microsoft is a company listed on NASDAQ with the ticker symbol MSFT.” can still be supported from a `structured_fact` citation. That citation has no evidence row. Market observations also have no document evidence id. Their support is the stored impact window.

Where a source already has an HTTPS URL, that URL is copied onto the document citation. A source with no URL stays without one. Market citation URLs stay null.

## 5. Entity and source consistency

Entity check: when the finding provenance has an entity id and the evidence snapshot has a non-empty `entity_ids` list, the claim entity must be in that list. Evidence for another company in the same article does not support the claim. Co-mention still does not create a relationship and does not count as factual support.

Source check: the evidence snapshot’s `source_id` must equal the citation’s `source_id`. A claim that cites source A while the evidence row belongs to source B is `unsupported`.

## 6. Market-intent completeness

`market_required` looks at the question, not at which companies are named.

It is true when the text contains a market-reaction phrase (`market reaction`, `market reacted`, `price reaction`, `investor reaction`, `stock price`, `share price`), or the word `market` together with `react` / `reaction` / `reacted`, or an instrument token (`stock`, `stocks`, `share`, `shares`). `market share` and `market shares` are removed from that instrument rule. Otherwise a reaction token (`react`, `rose` / `rise` / `risen`, `fell` / `fall` / `fallen`, `gained` / `gain` / `gains`, `dropped` / `drop`, `perform` / `performance` / `performed`, `trading` / `traded`) together with `how`, `why`, or `did` requires market. If the question says `business` and no instrument matched, it does not require market.

`What does Microsoft do?`, `What is Microsoft's partnership with NVIDIA?`, and `Tell me about NVIDIA's business.` do not require market. `How did NVIDIA perform around the recent news events` does, which is the Phase 13 skip.

If the detector is false, market stays optional. A plan that includes market is still accepted.

## 7. Planner behavior before and after

| Question | Before | After |
| --- | --- | --- |
| “How did NVIDIA perform around the recent news events in the corpus?” | The model could select only `news`. Market was skipped. | `required_domains` is `["market"]`. A plan without `market` is rejected once, then the fallback includes `market`. |
| “What was Microsoft's market reaction?” | Market was likely, but not required. | Market is required and was selected. |
| “What does Microsoft do?” | Market was optional. | Market is still optional. The detector does not add it and does not remove it. |
| “What is NVIDIA's partnership with Microsoft?” | Market was optional. | Market is not required. On the staging job the model did not select it, and the specialist was skipped. |

The live model can still add market to a company overview. That happened on the staging overview job. The completeness check did not override that choice.

## 8. Regression tests

`backend/tests/unit/research/test_evidence_integrity.py`

- A supported-looking FACT with an empty evidence id is `unsupported` (`missing_evidence`), and `evidence_ids` is empty.
- An evidence id whose snapshot source is not the citation source is `unsupported` (`evidence_source_mismatch`).
- An evidence id for the citation source whose entity list is another company is `unsupported` (`evidence_entity_mismatch`).
- A matching record for the same entity stays `supported`.
- A document OBSERVATION with an empty evidence id is `unsupported`.

`backend/tests/unit/research/test_market_intent.py` covers the seven required questions: Microsoft market reaction, NVIDIA stock reaction, Apple shares after the announcement, and “Why did NVIDIA's stock rise after the announcement?” require market. “What does Microsoft do?”, “What is Microsoft's partnership with NVIDIA?”, and “Tell me about NVIDIA's business.” do not. A news-only plan for the Microsoft market question is flagged incomplete. The fallback plan includes `market` and `required_domains == ["market"]`.

Local runs after the observation check:

- Research, retrieval, market, graph, and identity: 235 passed.
- Frontend `lib/researchDesk.test.ts`: 7 passed. `tsc --noEmit` and `next lint` were clean. Frontend code was not changed, and the frontend image was not rebuilt.

Phase 13 market tests still pass, including the renderer check that `0.015857…` becomes `+1.59%`.

## 9. Staging results

Account `225201316318`, cluster `quantpilot-staging`. The running image is `phase14b-api` / `phase14b-worker`, digest `sha256:1f5335119bde734f668439721dc1a720dfef37be35a99ef06bc7affd5d8fad67`.

API service `api` is task definition `quantpilot-staging-api:10`. Worker service `research-worker` is `quantpilot-staging-worker:15`. Both rollouts completed with one running task and zero failed tasks. `/health` reported `database: ok`. The frontend service was left on the Phase 10 image.

An earlier image, digest `sha256:8580eace6847a33956b9325513392b8f6f5df84502abc263538b651df27c29f5` (`quantpilot-staging-api:9`, `quantpilot-staging-worker:14`), applied the document chain to FACT, INFERENCE, and SCENARIO only. On that image, company and news observations could still be supported with a document citation and no evidence id. `evidence-v5` closes that skip. The table below is the `evidence-v5` matrix. Compact rows are in `eval/phase14_staging_matrix.json`.

Planner version on every job is `plan-v1`. Evidence runs are `evidence-v5`. Market runs are `market-v4`.

| Job | research_id | Status | required_domains | Selected | Market run | Resolved | Observations | Percent mismatches |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A. Microsoft market reaction | `efaa201a-d6f4-4fb5-857b-4073fe6e3e1d` | succeeded | market | market | succeeded | MSFT `a6d84c30-9814-454f-99a3-66e1cdeaf237` | 12, all MSFT | 0 |
| B. NVIDIA stock reaction | `a0bd238a-09be-4ef1-9a7d-9968dd00bfde` | succeeded | market | market | succeeded | NVDA `3462ac4e-98c0-417a-aa56-538b1d91e605` | 12, all NVDA | 0 |
| C. What does Microsoft do? | `aa229a47-8dc5-4bd0-bdfb-0894bf5e10c5` | succeeded | none | company, technology, market | succeeded | MSFT `a6d84c30-9814-454f-99a3-66e1cdeaf237` | 12, all MSFT | 0 |
| D. NVIDIA partnership with Microsoft | `51ba579b-f740-4030-a9e0-ee59f66dcd07` | succeeded | none | news, company | skipped | MSFT and NVDA | 0 | 0 |
| E. Apple market reaction | `7c015307-7311-4109-825b-7570c34c8008` | succeeded | market | market | succeeded | AAPL `2c509dc4-bf12-4c8f-ae08-4b96025cfe51` | 12, all AAPL | 0 |

Example windows, each equal to `round(stored_fraction * 100, 2)` for that same event:

| Job | Event | Window | Generated | Stored |
| --- | --- | --- | --- | --- |
| Microsoft | `031156cd-bf79-422d-98f2-7f5aa949ecb6` | +1D | +1.59% vs SPY +1.55% | +1.59% vs +1.55% |
| NVIDIA | same event | +1D | +2.30% vs SPY +1.55% | +2.30% vs +1.55% |
| Apple | same event | +1D | +0.85% vs SPY +1.55% | +0.85% vs +1.55% |

Every market observation on A, B, C, and E matched its stored asset percent and benchmark percent. Microsoft observations are MSFT, not NVDA. The deterministic renderer remains the source of the percent.

Job C did not require market. The model selected it anyway, and the specialist ran. The report’s only document-style key fact is the structured NASDAQ listing above. It has an empty evidence id because a `structured_fact` citation has no evidence row. Nineteen other claims on that job are unsupported and were left out of the factual sections.

Job D did not require market, the model did not select it, and the market specialist was skipped. All 17 claims are unsupported. The report has no verified factual section. That is the empty-evidence outcome for document text, not a finding that the partnership is absent from the corpus.

The Apple report contains 12 observations. The claims list stored 9 supported rows and no unsupported rows. The percent check was done on the 12 report observations.

## 10. Remaining limitations

News ingestion still does not create `EvidenceRecord` rows. A live article fact or document observation is unsupported until a matching evidence row exists. This phase does not backfill those rows.

A structured company listing can still be supported with an empty evidence id. A market observation is supported from the impact row, not from a document evidence id.

The market detector does not remove an optional market selection. “What does Microsoft do?” required no market domain and the specialist still ran, because the planner added it.

Phase 12 overclaims that were STILL ALLOWS remain STILL ALLOWS. hca-14 stays a false reject under the buy/sell/hold rule. `claim_id` is still `sha256(finding_id|status)[:16]` and is not globally unique. A market citation still has no top-level `entity_id`; identity is on `citation.market` and on observation provenance.

This is not a claim of factual accuracy. No further phase was started.
