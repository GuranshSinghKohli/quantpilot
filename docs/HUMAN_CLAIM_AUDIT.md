# Human claim audit rubric

This is the labeling guide for `eval/human_claim_audit_v1.json`. It is a manual check of stored research claims against the cited source and, for market claims, the stored impact row.

The audit records QuantPilot's `verification_status`. That status is not evidence. A claim is supported only when the opened source, or the stored impact row, supports the statement.

## What the evaluator reads

For each case:

- the claim text
- the research question
- the cited URL, when one is stored
- the article body at that URL
- for a market claim, `GET /api/events/{event_id}/impact`: entity, symbol, window, return, benchmark return
- the entity the sentence names

A valid URL, a citation id, or a mention of the company is not enough. The source has to support the statement.

"Microsoft partnered with Company X" is not fully supported by "Microsoft is evaluating Company X." Two companies in one article are not a partnership.

## Axes

### A. Factual correctness (`human_label`)

| Label | Meaning |
| --- | --- |
| `CORRECT` | The statement matches the source, or a market row, including the company and the figure. |
| `PARTIALLY_CORRECT` | A real piece of the source is present, and a material qualifier, cause, or extra premise is wrong or missing. |
| `INCORRECT` | The statement contradicts the source, names the wrong company, or states a number the cited row does not contain. |
| `NOT_VERIFIABLE` | The cited source could not be opened, and no stored row could decide the statement. |

A 100x unit error is `INCORRECT`. Printing the raw return `0.0134` as `0.0134%` does not match the stored `1.34%`.

### B. Evidence support (`human_support_level`)

| Label | Meaning |
| --- | --- |
| `FULLY_SUPPORTED` | The cited source or impact row states the claim. |
| `PARTIALLY_SUPPORTED` | The source supports part of the claim and not the rest. |
| `NOT_SUPPORTED` | The cited material does not support the statement as written. |
| `SOURCE_UNAVAILABLE` | The URL or impact row could not be read. |

### C. Citation correctness (`citation_correctness`)

| Label | Meaning |
| --- | --- |
| `CORRECT_CITATION` | The citation points at the source that contains the supporting passage, or at the impact row whose company and window are the ones in the sentence. |
| `WRONG_CITATION` | The citation points at a source or row that does not contain the statement. |
| `INSUFFICIENT_CITATION` | The claim has no citation, or the citation does not identify a checkable source. |
| `NO_CITATION_REQUIRED` | Not used in v1. |

A citation can be `CORRECT_CITATION` when the row is the right row and the prose misreads it. hca-29 is that case: the META row is the right row, and the percent sign is a calculation error.

### D. Entity correctness (`entity_correctness`)

| Label | Meaning |
| --- | --- |
| `CORRECT_ENTITY` | The company in the sentence is the company in the source. An index sentence that names no company is `CORRECT_ENTITY`. |
| `WRONG_ENTITY` | The sentence names a company the source does not support, or uses another company's figure. |
| `AMBIGUOUS` | The sentence says "the stock" or similar, and the question does not name a company, even if a symbol is stored on the citation. |

### E. Market correctness (`market_correctness`)

| Label | Meaning |
| --- | --- |
| `CORRECT_SECURITY` | The citation symbol is present and matches the impact row for the company whose return is stated. |
| `WRONG_SECURITY` | The citation symbol is a different company from the figure, or the citation stores no symbol. |
| `NOT_APPLICABLE` | The claim is not a market-window claim. |

Also check benchmark, event, window, direction, and the numeric value. Those checks live in `human_label`, `human_support_level`, and `error_types`. A correct symbol with a wrong percent is `CORRECT_SECURITY` and `INCORRECT`.

These staging citations often have `entity_id: null`. That field was not populated on the jobs in this audit. A null `entity_id` is recorded on the case. It is not treated as a match.

### F. Human confidence (`human_confidence`)

| Label | Meaning |
| --- | --- |
| `HIGH` | The passage or the impact row decides the label. |
| `MEDIUM` | The source supports the figure, and a small wording gap remains. |
| `LOW` | The source was thin or ambiguous. Not used in v1. |

## Inferences and scenarios

An inference is not incorrect only because the source never says the sentence.

It should be:

- labeled `INFERENCE`
- grounded in the cited evidence
- free of a factual premise the source does not support

A scenario should stay conditional. A conditional sentence that matches the source can be `CORRECT` and `FULLY_SUPPORTED` while QuantPilot's status stays `uncertain`. That is agreement, not a miss.

## Relationships

A co-mention is not a relationship. "Discussed" is not `PARTNERED_WITH`. Explicit language such as "proposed partnership" or "strategic relationships with" can support a prose claim. A supported sentence is still not a graph edge unless a relationship row exists. This audit did not find relationship rows for the sampled claims.

## Errors

`error_types` uses only categories that occurred. The first entry is the primary cause.

`wrong_entity`, `wrong_security`, `unsupported_claim`, `overclaim`, `citation_mismatch`, `source_ambiguity`, `relationship_inference`, `market_calculation`, `temporal_mismatch`, `stale_source`, `incomplete_context`, `other`.

`other` in v1 is the sourced Zacks Hold/Buy sentence that the investment-language check excluded. The sentence is in the article. The stored status is `unsupported`, and the claim has no citation.

## How status agreement is counted

Positive class for precision, recall, and F1: a `FACT` or `OBSERVATION` that is `CORRECT` and `FULLY_SUPPORTED`. QuantPilot's positive class is `supported`.

- Precision: supported claims that meet that human bar, divided by all supported claims in the sample.
- Recall: those human-positive claims that QuantPilot marked `supported`, divided by all human-positive claims in the sample.
- A sourced sentence rejected for investment language counts as a false negative on this definition. The results doc says so.

Strict status agreement, counted separately:

- `supported` agrees only with `CORRECT` and `FULLY_SUPPORTED`
- `uncertain` agrees when the finding is an `INFERENCE` or `SCENARIO`, the human label is not `INCORRECT`, and support is not `NOT_SUPPORTED`
- `unsupported` agrees when the human support is `NOT_SUPPORTED` or the human label is `INCORRECT`

The sample is a small manual audit. Do not attach a confidence interval or a significance test to these counts.

## Reproducibility

Each case stores `evaluation_id`, `research_id`, `claim_id` when the report had one, source URLs, and the impact snapshot used for market claims. `evaluation_id` is the unique key. `claim_id` repeats across jobs because it is a hash of `finding_id` and status.

Dataset version: `human-claim-audit-v1`. Evaluation date: 2026-10-07. Model recorded for these jobs: `gpt-4o-mini` (the research payload does not store the model name). Verifier: the deterministic checks in `backend/app/research/verify.py`, with `evidence-v2` on these jobs. Market calculation: `v1`, benchmark `SPY`.
