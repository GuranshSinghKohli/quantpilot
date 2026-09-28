# Research evaluation (Phase 4.2)

Development set only. **Not** OpenAI semantic quality and **not** human-judged accuracy.

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

Phase 4.1 retrieval recall/precision from the lexical-hash embedding provider remains a separate development number and must not be presented as semantic retrieval quality.
