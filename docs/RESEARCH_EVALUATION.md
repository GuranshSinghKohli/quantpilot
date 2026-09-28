# Research evaluation (Phase 4.3)

**DEVELOPMENT-ONLY. FAKE LLM. NOT HUMAN-JUDGED. NOT PRODUCTION MODEL QUALITY.**

```
python -m app.research evaluate
# or: make research-evaluate
```

Uses `FakeLLMProvider` + disposable in-memory SQLite. Seeds a handful of companies, news documents, one partnership edge, and one DEVELOPS edge.

## Fixture

24 cases in `app/research/eval_data.py`:

- company identity and evidenced partnerships
- technology / GPU language
- news events
- market questions with **no** impact rows (expect no market observation)
- multi-entity questions
- planted missing citations, unsupported partnerships, conflicts
- partial agent failure and malformed output
- transient provider failure that recovers

## Metrics reported

| Metric | Meaning |
|---|---|
| citation_coverage | share of visible findings that carry citations |
| unsupported_claim_rate | share of claims marked unsupported |
| verification_accuracy | planted cases behave as expected |
| structured_output_validity | 1.0 when FakeLLM returns valid `AgentFinding` payloads (or classified failure) |
| agent_completion_rate | runs with no specialist/unavailable flags |
| partial_result_rate | complementary rate |
| claim_precision / claim_recall | **finding types** vs `expected_types`, not statement match |

If ground truth for a case has no expected types, precision/recall ignore that case.

Phase 4.3 adds, without removing the metrics above:

| Metric | Meaning |
|---|---|
| report_citation_coverage | factual report items that carry a citation |
| factual_claim_verification_rate | same numerator; every factual item must be cited |
| unsupported_final_claim_rate | factual items whose text is an unsupported claim |
| conflict_preservation_rate | both sides kept when the guard marked a conflict |
| report_structure_validity | report present and factual items cited |
| specialist_selection_accuracy | selected names stay inside the fixed enum and the plan source is `llm` or `fallback` |
| partial_result_correctness | `expect_partial` matches `status == partial` |
| plan_validity | plan exists and names only allowed specialists |
| summary_evidence_coverage | executive-summary items that carry a citation |

`specialist_selection_accuracy` is a consistency check against the fixed enum. It is not a measure of semantic planning.

Phase 4.1 retrieval recall/precision from the lexical-hash embedding provider remains a separate development number and must not be presented as semantic retrieval quality.
