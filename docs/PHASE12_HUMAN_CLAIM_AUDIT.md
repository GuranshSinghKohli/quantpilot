# Phase 12 — human claim accuracy audit

In this 33-claim manual audit of stored staging reports, 14 claims were factually correct and fully supported by the opened source or the stored impact row. QuantPilot had marked 26 of the 33 as supported. Twelve of those 26 met the strict human bar. Seven supported claims were factually incorrect.

The largest measured failures are security binding and market arithmetic. Two Microsoft questions cited an NVDA window while the sentence's 1.59% matches MSFT on the same event. One unscoped sentence cited NVDA and stated Apple's return. One META window was printed about 100 times too small. One NVIDIA +1 return was taken from a different event than the citation. Two supported relationship sentences named Microsoft in articles that do not mention Microsoft.

This is a small manual audit of 33 claims from 10 completed reports. It is not an estimate of overall system accuracy. No confidence interval is reported.

The verifier, planner, and market code were not changed. Findings below are the baseline.

## 1. Objective

Compare QuantPilot's stored verification status with a human reading of the cited source and the stored market row.

The questions were:

- Does a supported claim match the source?
- Is an uncertain claim actually an inference or a scenario, and is it grounded?
- Does an unsupported claim fail the source?
- Does the citation contain the statement?
- Is the company the company in the source?
- Does a market claim use the security, event, window, benchmark, and number in the stored impact row?
- Does the report add a relationship or a fact the source does not state?

## 2. Dataset selection

Source: completed `succeeded` jobs on staging, read with `GET /api/research/{id}` on 2026-10-07. Market numbers were checked with `GET /api/events/{id}/impact`. Historical claims were not edited.

Thirteen reports were opened. Three had zero claims and are listed because they bound the relationship and company questions. Ten reports contributed the 33 claims. The jobs were real `gpt-4o-mini` runs. FakeLLM output was not used.

The research payload does not store the model name. Phase 9 and Phase 10.1 records identify these jobs as `gpt-4o-mini`. Prompt versions on the sampled jobs are `master-v3` or `master-v4`, `news-v2`, `company-v2`, `technology-v2` where that agent ran, `market-v2`, and `evidence-v2`. Market calculation version is `v1`. Benchmark is `SPY`.

These jobs ran before the Phase 11 ticker-binding fix. Market citations in the sample have `entity_id: null`. Phase 9 market citations also omit symbol and windows. The audit scores the stored rows.

| Research id | Finished (UTC) | Claims in job | Sampled | Question |
| --- | --- | --- | --- | --- |
| `e124f706-02ec-493d-9b07-68b7427bc0b2` | 2026-10-04 01:35 | 0 | 0 | Vanguard VT event |
| `e10971ee-af75-4550-a8ca-3e71aeada5bb` | 2026-10-04 01:36 | 0 | 0 | NVIDIA–Microsoft relationships |
| `2f15726e-00b0-49c8-909d-361cb6a56bb4` | 2026-10-04 01:36 | 6 | 1 | NVIDIA daily market observations |
| `20afe096-ec23-4dd1-8f64-0fb58b97b49e` | 2026-10-04 01:37 | 0 | 0 | Meta Platforms articles |
| `55909e3b-85e0-43ea-a480-01224f7671b1` | 2026-10-04 01:37 | 6 | 1 | Explicit partnerships in the corpus |
| `05a7920d-7577-4680-b4f3-5f68540eb450` | 2026-10-05 20:44 | 22 | 7 | NVIDIA news and market movement |
| `f1ab9c21-9a47-4ae8-b457-047787c4e863` | 2026-10-05 20:45 | 14 | 1 | NVIDIA performance around news |
| `62295884-c072-4a0d-af17-6fe30066983f` | 2026-10-05 20:46 | 16 | 7 | Microsoft news and market performance |
| `e7462afd-f4f6-4189-9cdc-8a232bd51720` | 2026-10-05 20:46 | 8 | 3 | Companies Microsoft partnered with |
| `7ce0cced-7276-429e-b546-42bc88225a06` | 2026-10-05 20:47 | 27 | 7 | What Apple does |
| `eca9ce3a-a840-4909-bb81-a9a770ee1f39` | 2026-10-05 20:47 | 31 | 4 | "Tell me about the situation." |
| `72cf5986-c952-477b-8376-165a7035fd75` | 2026-10-05 20:50 | 15 | 1 | Microsoft news and market performance, rerun |
| `11656c5d-c849-4ff6-8659-fe6f1044d62d` | 2026-10-05 20:54 | 20 | 1 | Selected NVIDIA event and the market observation |

Sampled claim ids are `hca-01` through `hca-33` in `eval/human_claim_audit_v1.json`. The mix covers company facts, a news/event window, market observations, relationship sentences, inferences, scenarios, and two unsupported claims that were left out of the report. Companies in the sample include NVIDIA, Microsoft, Apple, AMD, Meta, and an S&P 500 index sentence. Sources are Motley Fool and Zacks articles plus `v1` impact rows.

`claim_id` is not a global identifier. It is a 16-character hash of `finding_id` and status, so the same id is attached to different statements. `evaluation_id` plus `research_id` identifies a case. The two unsupported claims have no `claim_id` because they were excluded from the report.

## 3. Labeling methodology

The rubric is `docs/HUMAN_CLAIM_AUDIT.md`.

Each article URL on a sampled claim was fetched on 2026-10-07 (21 pages, HTTP 200) and read for the sentence, not for a company mention. Market claims were checked against the impact payload. Returns there are fractions: `0.022990` is 2.30%.

QuantPilot's status was visible on the stored report. The support judgment used the article text and the impact row. There was one labeler. A second blind rater was not used.

Inferences and scenarios were allowed to be reasonable without a verbatim sentence, provided they stayed labeled as such and did not add an unsupported factual premise.

## 4. 33-claim results

| Human factual label | Count |
| --- | --- |
| `CORRECT` | 14 |
| `PARTIALLY_CORRECT` | 12 |
| `INCORRECT` | 7 |
| `NOT_VERIFIABLE` | 0 |

| Human evidence label | Count |
| --- | --- |
| `FULLY_SUPPORTED` | 14 |
| `PARTIALLY_SUPPORTED` | 11 |
| `NOT_SUPPORTED` | 8 |
| `SOURCE_UNAVAILABLE` | 0 |

| Citation | Count |
| --- | --- |
| `CORRECT_CITATION` | 25 |
| `WRONG_CITATION` | 6 |
| `INSUFFICIENT_CITATION` | 2 |

| Entity | Count |
| --- | --- |
| `CORRECT_ENTITY` | 26 |
| `WRONG_ENTITY` | 6 |
| `AMBIGUOUS` | 1 |

QuantPilot status in the sample: supported 26, uncertain 5, unsupported 2, conflicting 0.

The 14 fully correct claims include company facts (dividend dollars, forward P/E, Services revenue, Emmys, Apple Watch dates), two relationship sentences the articles actually state, one index-concentration sentence, and two NVDA +1 windows that match the stored row (`hca-07`, `hca-32`).

The 7 incorrect claims are `hca-11`, `hca-16`, `hca-17`, `hca-28`, `hca-29`, `hca-31`, and `hca-33`. All seven were stored as supported.

## 5. QuantPilot vs human comparison

Strict status agreement is 18 of 33.

- Supported agrees only when the human label is `CORRECT` and `FULLY_SUPPORTED`: 12 of 26.
- Uncertain agrees when the claim is an inference or scenario, is not incorrect, and is not unsupported by the source: 5 of 5. Two of those five still overclaim a premise (`hca-23`, `hca-26`). The status `uncertain` is still the right bucket.
- Unsupported agrees when the human mark is `NOT_SUPPORTED` or `INCORRECT`: 1 of 2. `hca-30` agrees. `hca-14` does not.

`hca-14` is the disagreement on the unsupported side. The Zacks article says Oracle is Zacks Rank #2 (Buy) and Microsoft is Zacks Rank #3 (Hold). The claim was excluded from the report with no citation. The wording matches the investment-language filter in the verifier. The sentence is in the source. This is recorded as a finding. The filter was not changed.

## 6. Metrics

Definitions are in the rubric. Positive class: `FACT` or `OBSERVATION`, human `CORRECT` and `FULLY_SUPPORTED`. Predicted positive: QuantPilot `supported`.

| Metric | Count | Rate in this audit |
| --- | --- | --- |
| Factual accuracy (`CORRECT`) | 14 / 33 | 0.42 |
| Correct or partial | 26 / 33 | 0.79 |
| Full evidence support | 14 / 33 | 0.42 |
| Partial evidence support | 11 / 33 | 0.33 |
| Human not-supported | 8 / 33 | 0.24 |
| Citation correct | 25 / 33 | 0.76 |
| Entity correct | 26 / 33 | 0.79 |
| Market security correct | 3 / 7 | 0.43 |
| QuantPilot unsupported | 2 / 33 | 0.06 |
| Strict status agreement | 18 / 33 | 0.55 |

Precision 12/26. Recall 12/13. F1 8/13 (0.615). The one false negative on this definition is `hca-14`.

Of the 26 supported claims, 7 are factually incorrect. That is the false-support count a reader should see beside the F1.

## 7. Confusion matrix

Rows are QuantPilot status. Columns are the human factual label. `conflicting` did not occur. `NOT_VERIFIABLE` did not occur.

| QuantPilot status | Correct | Partially correct | Incorrect |
| --- | --- | --- | --- |
| Supported | 12 | 7 | 7 |
| Uncertain | 1 | 4 | 0 |
| Unsupported | 1 | 1 | 0 |

The uncertain/correct cell is `hca-24`, a scenario whose conditional wording matches the article. Uncertain is the appropriate status for that scenario, so the cell is agreement under the rule in section 5, even though the factual column says correct.

## 8. Error taxonomy

Primary cause, one per claim. Fifteen claims have no error tag: they are fully correct, or they are grounded inferences/scenarios left uncertain.

| Primary error | Count | Cases |
| --- | --- | --- |
| `overclaim` | 8 | `hca-05`, `hca-06`, `hca-09`, `hca-15`, `hca-22`, `hca-23`, `hca-26`, `hca-30` |
| `wrong_security` | 3 | `hca-11`, `hca-28`, `hca-33` |
| `incomplete_context` | 2 | `hca-04`, `hca-08` |
| `wrong_entity` | 1 | `hca-16` |
| `citation_mismatch` | 1 | `hca-17` |
| `market_calculation` | 1 | `hca-29` |
| `temporal_mismatch` | 1 | `hca-31` |
| `other` | 1 | `hca-14` |

Secondary tags, not added to the primary counts: `citation_mismatch` also on `hca-11` and `hca-33`; `market_calculation` also on `hca-28`; `relationship_inference` also on `hca-17`.

`hca-31` is `WRONG_SECURITY` on the market axis because the citation stored no symbol, and `temporal_mismatch` as the primary content error because the 1.34% is NVDA's return on a different event.

## 9. Examples of incorrect claims

**hca-11 and hca-33.** Question: recent Microsoft news and market performance. `hca-11` says the stock returned 1.59% and the benchmark return was 1.55%. `hca-33` says the benchmark returned 1.55%. Citation symbol on both: NVDA. Event `031156cd-bf79-422d-98f2-7f5aa949ecb6`. Stored NVDA +1D return `0.022990` (2.30%). Stored MSFT +1D return on that same event `0.015857` (1.59%). Benchmark `0.015505` (1.55%). The sentence matches Microsoft. The citation is NVIDIA. `hca-33` is a later rerun of the same question with the same mismatch.

**hca-28.** Unscoped question. Citation symbol NVDA on the same event. Stated return `0.0085%` and benchmark `0.0155%`. Apple's +1D return on that event is `0.008479`. The benchmark fraction is `0.015505`. The sentence uses Apple's return and prints both raw fractions with a percent sign, under an NVDA citation.

**hca-16.** "Berkshire Hathaway has concentrated approximately 31% of its stock portfolio in AI-focused companies, including Microsoft." The cited article is about two stocks, Alphabet and Apple. Microsoft does not appear in the article.

**hca-17.** "Microsoft is mentioned in the context of AI alongside NVIDIA and Alphabet." The cited robotics article does not mention Microsoft.

## 10. Examples of correctly supported claims

**hca-01.** "Nvidia has returned $46 billion to shareholders through dividends and buybacks in the first half of its fiscal year." The article says that through the first two quarters of the current fiscal year, Nvidia returned around $46 billion through dividends and stock buybacks.

**hca-10.** Microsoft forward P/E 25.77 and Oracle forward P/E 17.97 are both stated in the cited Zacks article.

**hca-18.** Apple Services revenue rose 12% to a June-quarter record of $30.74 billion. The article states that.

**hca-21.** "Apple is reportedly considering developing its own AI servers in partnership with Nvidia." The article says The Information reported that Apple was considering its own AI server, and that in its proposed partnership with Nvidia, Apple would use NVLink Fusion. The claim does not say the deal closed.

**hca-07.** NVIDIA question. Citation symbol NVDA. +1D `0.022990` (2.30%) versus SPY `0.015505` (1.55%). The number matches. The event article is a QQQ column. The return claim matches the NVDA impact row. `hca-32` matches the same row on a later NVIDIA question.

## 11. Market-security verification

Seven market claims. Impact rows used:

| Case | Question names | Cited symbol | Cited event +1D | What the sentence matches |
| --- | --- | --- | --- | --- |
| `hca-07` | NVIDIA | NVDA | NVDA 2.30%, SPY 1.55% | That NVDA row |
| `hca-32` | NVIDIA | NVDA | NVDA 2.30%, SPY 1.55% | That NVDA row |
| `hca-29` | none | META | META 1.34%, SPY 1.13% | The META fractions printed as 0.0134% and 0.0113% |
| `hca-11` | Microsoft | NVDA | NVDA 2.30%, SPY 1.55% | MSFT 1.59% on the same event |
| `hca-33` | Microsoft | NVDA | NVDA 2.30%, SPY 1.55% | MSFT 1.59% on the same event |
| `hca-28` | none | NVDA | NVDA 2.30%, SPY 1.55% | AAPL fraction 0.008479, printed as 0.0085% |
| `hca-31` | NVIDIA | none stored | NVDA 2.30%, SPY 1.55% on the cited event | NVDA 1.34% on event `0acaefb4-1723-444c-9933-e9a709f5329f`, whose benchmark is -0.12%, paired with the cited event's 1.55% benchmark |

In this audit, 3 of 7 market claims have `CORRECT_SECURITY` (`hca-07`, `hca-29`, `hca-32`). `hca-29` still fails the number, because 0.0134% is not 1.34%. Only `hca-07` and `hca-32` match both the security and the stated percent.

Every market citation in the sample has `entity_id: null`. The Phase 11 check would reject a market citation that cannot show entity and symbol. That check was not on the workers that produced these reports. This audit measures the stored Phase 9 and Phase 10.1 behavior. It does not measure the undeployed fix.

## 12. Relationship verification

The staged corpus had no relationship rows. The empty NVIDIA–Microsoft job (`e10971ee-af75-4550-a8ca-3e71aeada5bb`) returned no claims. That is a true negative for a graph edge. Prose still asserted relationships.

Passed:

- `hca-21`. Proposed Apple–Nvidia partnership, explicitly "proposed partnership" and "considering."
- `hca-25`. "AMD has strategic partnerships with OpenAI, Meta, and Anthropic." The article says "Strategic relationships with OpenAI, Meta and Anthropic."

Failed:

- `hca-16`. Microsoft inserted into a Berkshire article about Alphabet and Apple.
- `hca-17`. Microsoft, NVIDIA, and Alphabet treated as a joint AI mention. The article does not mention Microsoft.

`hca-15` does not assert a partnership. It stretches a headline grouping of Microsoft, NVIDIA, and Apple into "attractive investment opportunity." That is an overclaim, not a relationship edge.

A co-mention was not written as a graph edge. The failure is in the prose.

## 13. Limitations

- Thirty-three claims, one labeler, ten reports, five named companies plus one index sentence.
- The labeler could see QuantPilot's status. Support was decided from the article and the impact row.
- The sample prefers claims that looked checkable and diverse. It is not a random draw of every stored claim.
- Three opened reports had no claims and are outside the 33.
- Some source sentences are opinions or analyst targets. A faithful restatement can still be a poor `FACT`.
- Stored news chunks are headline and description. The audit read the full article at the cited URL.
- Phase 11 was not deployed. These rows cannot show that the new binding check is active in staging.
- No confidence interval and no significance test.

## 14. Recommended fixes

Not implemented in this phase.

1. Deploy the Phase 11 ticker binding already written locally. In this audit, Microsoft questions still received NVDA citations, and citations still omit `entity_id`.
2. Print market percents as the fraction times 100. `hca-28` and `hca-29` show the raw fraction with a percent sign.
3. Keep a return and its benchmark on the same event and the same security. `hca-31` mixes NVDA's +1 return from event `0acaefb4` with the benchmark from event `031156cd`.
4. Keep segment names. Intelligent Cloud growth of 32% was stated as Azure growth (`hca-09`). Latest-quarter revenue growth was stated without the quarter (`hca-04`). Dividends declared were stated as paid (`hca-08`).
5. Reject a relationship sentence when the cited article does not name the company. `hca-16` and `hca-17` are the examples. A co-mention is still not a partnership.
6. Record a sourced Hold/Buy sentence separately from the investment-language exclusion. `hca-14` is in the Zacks article and was stored as unsupported with no citation. The filter can stay. The audit trail should show "excluded as investment language" rather than "the source does not support this."
7. Keep a single named price target, and an opinion, out of supported `FACT`. `hca-26` turns Simon Leopold's $515 target into "analysts." `hca-06` and `hca-22` restate opinion as fact.
8. Stop using `claim_id` alone to identify a claim across jobs. It repeats.

## Tooling

`eval/validate_human_audit.py` checks the dataset shape and reprints these counts. It does not import the research pipeline.

Pipeline code was not modified. After the dataset was added: ticker-binding tests, 9 passed; evidence, claim-coverage, and market unit tests, 50 passed. The frontend was not changed.
