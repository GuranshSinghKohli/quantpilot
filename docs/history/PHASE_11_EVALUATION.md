# QuantPilot Phase 11 — Historical Evaluation, Benchmarking & AI System Validation

**Scope:** A reproducible evaluation framework for Phases 1–10. Deployment, frontend redesign, CrewAI, LangGraph replacement, OpenClaw expansion, trading, and production-logic tuning for scores were **not** implemented.

**Source of truth:** Implementation under `evals/` plus `backend/tests/test_investigation_phase11_evaluation.py`.

---

## 1. Purpose

Move from “the agent looks impressive” to “each stage is measurable.”

The evaluator scores **components**, not whether the final paragraph sounds good:

```
EVENT → MATERIALITY → HYPOTHESES → EVIDENCE
  → VERIFICATION → DEVIL'S ADVOCATE → CONCLUSION
  → ALERTS → MEMORY → TOOL ROUTING
```

---

## 2. Evaluation architecture

```
evals/datasets/catalog.py     historical cases (public card)
PackedCase.truth              ground truth (evaluator only)
PackedCase.fixture            recorded inputs / SUT hypotheses
evals/replay.py               production functions, isolated DB
evals/score.py                deterministic metrics
evals/run.py                  CLI + reports
```

Command:

```
PYTHONPATH=backend python -m evals.run --dataset v1
PYTHONPATH=backend python -m evals.run --dataset v1 --compare-baseline
PYTHONPATH=backend python -m evals.run --dataset v1 --all-ablations
```

Default mode is **deterministic replay**: no live OpenAI, no live OpenClaw, no production database writes.

---

## 3. Dataset

`v1` is 25 curated cases (quality over a fake 100). Types include earnings, guidance, regulatory, macro, sector, short-report, unusual volume, insufficient-evidence, planted verification flaws, company memory, MCP routing, and alerts.

Documented in `evals/datasets/README.md`. The loader can grow to 100+ without changing the runner.

---

## 4. Ground truth

Each case allows **multiple acceptable explanations** (phrase lists, not one gold sentence). Unacceptable phrases catch implausible leaps. Ground truth is never included in `public_case_view()`.

---

## 5. Temporal cutoff

Every case has `cutoff_time`. `evals.temporal.filter_evidence` drops `published_at > cutoff` unless `allow_retrospective`. The January 30 recap on `nvda-2024-01-24-hard` is the regression test.

---

## 6–8. Event and materiality

Replay calls `events_from_trigger`, `events_from_filings`, `events_from_fundamentals`, and `assess_investigation_materiality`. Metrics: event-type hit, investigate precision/recall/F1, material-event recall, false investigation rate.

---

## 9. Hypothesis metrics

Token-overlap (not an LLM): hypothesis recall, top-1/3/5 coverage, redundancy, unsupported-hypothesis rate.

---

## 10–11. Evidence and citations

Deterministic: primary-source rate, relevance, contradiction coverage, citation validity, support accuracy, **fabricated citation rate** (target 0% on clean cases; planted cases measure the leak).

---

## 12. Hallucination

`UNSUPPORTED_CLAIM_RATE`, `HALLUCINATION_RATE`, `APPROPRIATE_UNCERTAINTY_RATE`. `halluc-no-evidence` rewards “I don’t know,” not a secret meeting.

---

## 13–14. Verification and Devil's Advocate

Planted flaws (`CAUSAL_OVERREACH`, `TEMPORAL_LEAK`, `UNSUPPORTED`) are scored by whether `verification_engine` issues mention them. DA is scored on counterargument presence, contradiction handling, alternatives, and missing-evidence flags — not length.

---

## 15–16. Conclusion and calibration

Final `conclusion_strength` must fall in the allowed set (`SUPPORTED` … `INSUFFICIENT_EVIDENCE`). Confidence labels are **support labels**, not probability calibration.

---

## 17–18. Alerts and monitoring

`decide_notify` + investigation gate on labeled noise vs material cases. Replay is a historical day → snapshot → gate → notify path. Production sweep behavior is unchanged.

---

## 19. Company memory

Isolated SQLite. `memory-demand-shift` seeds two management statements and checks `detect_management_trend` / retrieve.

---

## 20–22. OpenClaw, MCP, tool efficiency

Recorded traces only. `browser-ir-pdf` checks `investor.nvidia.com`. `mcp-sec-no-browser` fails if a browser tool was invoked. Counts: tool / MCP / browser / duplicate / LLM calls (from the fixture).

---

## 23–24. Latency and cost

Recorded `latency_ms` percentiles (P50/P95/P99). Cost proxies: LLM calls, tokens, browser tasks, iterations. No invented USD prices.

---

## 25. Baseline

`evals/baseline.py`: first snippet is “the cause,” HIGH confidence, **no cutoff**. Compared via `--compare-baseline`. QuantPilot is not assumed to win every metric.

---

## 26. Ablations

`full | no_da | no_memory | no_browser | no_verification | baseline`

`--all-ablations` writes one report folder each.

---

## 27–29. Reproducibility and result format

Each `EvaluationRun` stores `run_id`, `git_commit`, `dataset_version`, `benchmark_version`, `model`, timestamp, configuration, per-case actuals, and errors.

---

## 30–31. Reporting and failure analysis

Markdown + JSON under `evals/reports/`. Failures are grouped: `EVENT_DETECTION`, `MATERIALITY`, `HYPOTHESIS`, `EVIDENCE`, `VERIFICATION`, `DEVILS_ADVOCATE`/`SYNTHESIS`, `MEMORY`, `BROWSER`, `TOOL_ROUTING`, `ALERT`.

---

## 32–33. Human review and LLM-as-judge

Human rubric 1–5 is documented and **not** filled by the automated runner (labeled HUMAN-EVALUATED).

`QUANTPILOT_EVAL_LLM_JUDGE=1` may score subjective dimensions only. Never citations, URLs, timestamps, or IDs.

---

## 34–35. Dataset quality and leakage

Mixed difficulty. Future sources, future memory, and expected answers are withheld from production calls.

---

## 36. Security

No API keys in the dataset. Reports must not include secrets.

---

## 37. Testing the framework

`backend/tests/test_investigation_phase11_evaluation.py` covers dataset validity, cutoff, metrics, isolation, reproducibility, and a full v1 run.

---

## 38. Limitations

- Replay uses recorded hypotheses for generation quality; live LLM investigation is opt-in later, not CI.
- 25 cases, not 100. Expand the catalog; do not pad with easy clones.
- `evidence_relevance` / `claim_support_rate` use strict phrase overlap of the full hypothesis against the evidence corpus. Long hedged conclusions score near zero even when the sources are relevant; do not read those two numbers as source-quality scores.
- Some official URLs are IR homepages when a permalink is not required for the metric.
- Alert/notify scores depend on current materiality thresholds; do not retune production to chase the benchmark.
- Phase 12 (harden/deploy) was not implemented.
