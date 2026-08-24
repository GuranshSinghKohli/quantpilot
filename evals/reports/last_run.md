# QuantPilot evaluation — v1

- run_id: `20260824T074438Z-fe9d05fc`
- git_commit: `1194ec3d8e2b168a031b2bb24fa3a0659d0cfd1b`
- benchmark: `phase11-v1`
- model: `none (deterministic replay)`
- timestamp: `2026-08-24T07:44:38.628281+00:00`
- configuration: `{"dataset": "v1", "ablation": "full", "ablation_note": "QuantPilot multi-stage (verify + DA + memory + cutoff)", "mode": "deterministic_replay", "isolated_db": true}`
- cases: 25

Numbers below are measured. None are placeholders.

## Aggregate (AUTOMATED)

- **alert_f1**: 0.778
- **alert_precision**: 0.636
- **alert_recall**: 1.000
- **appropriate_uncertainty_rate**: 0.880
- **avg_browser_calls**: 0.040
- **avg_llm_calls**: 0.160
- **avg_tool_calls**: 0.280
- **cases**: 25.000
- **challenge_usefulness**: 0.940
- **citation_support_accuracy**: 0.960
- **citation_validity**: 0.947
- **claim_support_rate**: 0.040
- **conclusion_label_match**: 0.800
- **contradiction_coverage**: 0.880
- **contradiction_detection**: 1.000
- **counterargument_relevance**: 1.000
- **duplicate_tool_call_rate**: 0.000
- **event_type_hit**: 1.000
- **evidence_relevance**: 0.040
- **fabricated_citation_rate**: 0.053
- **false_investigation_rate**: 0.000
- **hallucination_rate**: 0.173
- **hypothesis_recall**: 0.960
- **investigate_f1**: 1.000
- **investigate_precision**: 1.000
- **investigate_recall**: 1.000
- **latency_mean_ms**: 168.000
- **latency_p50_ms**: 0.000
- **latency_p95_ms**: 0.000
- **latency_p99_ms**: 4200.000
- **material_event_recall**: 1.000
- **memory_conflict_detection**: 1.000
- **memory_retrieval_recall**: 0.987
- **primary_source_rate**: 0.780
- **tool_routing_accuracy**: 1.000
- **top1_coverage**: 0.960
- **top3_coverage**: 0.960
- **top5_coverage**: 0.960
- **unsupported_claim_rate**: 0.120
- **unsupported_hypothesis_rate**: 0.000
- **verification_precision**: 0.960
- **verification_recall**: 0.960

## Failures

- EVIDENCE: 2
- HYPOTHESIS: 1
- SYNTHESIS: 5
- VERIFICATION: 1

- `meta-2022-02-03-earnings` / SYNTHESIS: expected=['SUPPORTED', 'PARTIALLY_SUPPORTED'] actual=ROBUST
- `gme-2021-01-27-squeeze` / SYNTHESIS: expected=['PARTIALLY_SUPPORTED', 'UNCERTAIN'] actual=SUPPORTED
- `wfc-2023-03-13-sector` / SYNTHESIS: expected=['PARTIALLY_SUPPORTED', 'UNCERTAIN'] actual=INSUFFICIENT_EVIDENCE
- `xom-2022-03-08-oil` / SYNTHESIS: expected=['PARTIALLY_SUPPORTED', 'SUPPORTED'] actual=INSUFFICIENT_EVIDENCE
- `aapl-2024-09-09-event` / HYPOTHESIS: expected=['product event', 'iphone'] actual=['A product announcement without a material idiosyncratic move does not clear the investigation bar.']
- `nvda-2024-01-24-hard` / EVIDENCE: expected=no fabricated citations actual=0.3333333333333333
- `halluc-no-evidence` / EVIDENCE: expected=no fabricated citations actual=1.0
- `verify-causal-overreach` / VERIFICATION: expected=['CAUSAL_OVERREACH'] actual=['Domain www.microsoft.com is not an official primary-source host.', 'Contradicts or weakens the claim: Microsoft earnings: revenue exceeded consensus', 'Contradicts or weakens the claim: Microsoft earnings: revenue exceeded consensus', 'No strongest alternative was ranked.', 'Support is post-event and cannot silently prove causation.', 'No primary-source regulatory announcement found.', 'Support is post-event and cannot silently prove causation.', 'No strongest alternative was ranked.', 'Support is post-event and cannot silently prove causation.']
- `verify-causal-overreach` / SYNTHESIS: expected=['PARTIALLY_SUPPORTED', 'UNCERTAIN', 'INSUFFICIENT_EVIDENCE'] actual=SUPPORTED

## Notes

- AUTOMATED metrics only. HUMAN-EVALUATED fields were not filled.
- LLM-as-judge was not used for citation/URL/timestamp/ID checks.
- Ground truth was not passed to production investigation functions.
- Future-dated sources were dropped unless allow_retrospective.
- Ablation=full: QuantPilot multi-stage (verify + DA + memory + cutoff)
- This is a recorded-replay benchmark, not a live OpenAI/OpenClaw crawl.

## Human evaluation

Not collected in this automated run. Rubric 1–5 for hypothesis, evidence, conclusion, and Devil's Advocate usefulness is reserved for HUMAN-EVALUATED review.

## LLM-as-judge

Disabled unless `QUANTPILOT_EVAL_LLM_JUDGE=1`. Never used for citation existence, URLs, timestamps, or evidence IDs.

