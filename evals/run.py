"""python -m evals.run --dataset v1

Default mode is isolated deterministic replay through production components.
Does not write to the production database.
"""

from __future__ import annotations

import argparse
import statistics
from typing import Dict, List

from evals import BENCHMARK_VERSION, DATASET_VERSION
from evals.ablations import ABLATIONS, describe
from evals.dataset import load_dataset
from evals.metrics.classification import binary_prf
from evals.metrics.latency import latency_metrics
from evals.replay import replay_case
from evals.reports.writer import write_report
from evals.reproduce import run_metadata
from evals.schemas import CaseScore, EvaluationRun
from evals.score import score_replay
from evals._path import ROOT


def _mean(rows: List[CaseScore], key: str) -> float:
    vals = [c.metrics[key] for c in rows if key in c.metrics]
    return float(statistics.fmean(vals)) if vals else 0.0


def aggregate(rows: List[CaseScore]) -> Dict[str, float]:
    inv_t = [bool(c.actual.get("should_investigate")) for c in rows]
    # reconstruct truth from investigate_match + actual
    inv_true = []
    for c in rows:
        pred = bool(c.actual.get("should_investigate"))
        match = c.metrics.get("investigate_match", 0) >= 1
        inv_true.append(pred if match else (not pred))
    inv = binary_prf(inv_true, inv_t)
    notify_t = [bool(c.actual.get("should_notify")) for c in rows]
    notify_true = []
    for c in rows:
        pred = bool(c.actual.get("should_notify"))
        match = c.metrics.get("notify_match", 0) >= 1
        notify_true.append(pred if match else (not pred))
    alerts = binary_prf(notify_true, notify_t)
    totals = [float((c.latency_ms or {}).get("total") or 0.0) for c in rows]
    out = {
        "cases": float(len(rows)),
        "event_type_hit": _mean(rows, "event_type_hit"),
        "material_event_recall": inv["recall"],
        "investigate_precision": inv["precision"],
        "investigate_recall": inv["recall"],
        "investigate_f1": inv["f1"],
        "false_investigation_rate": 1.0 - inv["precision"] if inv["precision"] or inv["tp"] else 0.0,
        "hypothesis_recall": _mean(rows, "hypothesis_recall"),
        "top1_coverage": _mean(rows, "top1_coverage"),
        "top3_coverage": _mean(rows, "top3_coverage"),
        "top5_coverage": _mean(rows, "top5_coverage"),
        "unsupported_hypothesis_rate": _mean(rows, "unsupported_hypothesis_rate"),
        "primary_source_rate": _mean(rows, "primary_source_rate"),
        "evidence_relevance": _mean(rows, "evidence_relevance"),
        "claim_support_rate": _mean(rows, "claim_support_rate"),
        "contradiction_coverage": _mean(rows, "contradiction_coverage"),
        "citation_validity": _mean(rows, "citation_validity"),
        "citation_support_accuracy": _mean(rows, "citation_support_accuracy"),
        "fabricated_citation_rate": _mean(rows, "fabricated_citation_rate"),
        "unsupported_claim_rate": _mean(rows, "unsupported_claim_rate"),
        "hallucination_rate": _mean(rows, "hallucination_rate"),
        "appropriate_uncertainty_rate": _mean(rows, "appropriate_uncertainty_rate"),
        "verification_precision": _mean(rows, "verification_precision"),
        "verification_recall": _mean(rows, "verification_recall"),
        "counterargument_relevance": _mean(rows, "counterargument_relevance"),
        "contradiction_detection": _mean(rows, "contradiction_detection"),
        "challenge_usefulness": _mean(rows, "challenge_usefulness"),
        "conclusion_label_match": _mean(rows, "conclusion_label_match"),
        "alert_precision": alerts["precision"],
        "alert_recall": alerts["recall"],
        "alert_f1": alerts["f1"],
        "memory_retrieval_recall": _mean(rows, "memory_retrieval_recall"),
        "memory_conflict_detection": _mean(rows, "memory_conflict_detection"),
        "tool_routing_accuracy": _mean(rows, "tool_routing_accuracy"),
        "avg_tool_calls": _mean(rows, "tool_calls"),
        "avg_llm_calls": _mean(rows, "llm_calls"),
        "avg_browser_calls": _mean(rows, "browser_calls"),
        "duplicate_tool_call_rate": _mean(rows, "duplicate_tool_call_rate"),
        **latency_metrics(totals),
    }
    return out


def execute(
    *,
    dataset: str = "v1",
    ablation: str = "full",
    case_id: str = "",
) -> EvaluationRun:
    packs = load_dataset(dataset)
    if case_id:
        packs = [p for p in packs if p.case.case_id == case_id]
        if not packs:
            raise SystemExit(f"Unknown case_id {case_id}")
    meta = run_metadata(
        {
            "dataset": dataset,
            "ablation": ablation,
            "ablation_note": describe(ablation),
            "mode": "deterministic_replay",
            "isolated_db": True,
        }
    )
    scores: List[CaseScore] = []
    errors: List[str] = []
    failures: List[dict] = []
    for pack in packs:
        try:
            actual = replay_case(pack, ablation=ablation)
            # Strip any accidental truth leakage from actual.
            actual.pop("truth", None)
            score = score_replay(pack, actual)
            scores.append(score)
            for fail in score.failures:
                failures.append({"case_id": pack.case.case_id, **fail})
        except Exception as exc:
            errors.append(f"{pack.case.case_id}: {exc}")
    notes = [
        "AUTOMATED metrics only. HUMAN-EVALUATED fields were not filled.",
        "LLM-as-judge was not used for citation/URL/timestamp/ID checks.",
        "Ground truth was not passed to production investigation functions.",
        "Future-dated sources were dropped unless allow_retrospective.",
        f"Ablation={ablation}: {describe(ablation)}",
        "This is a recorded-replay benchmark, not a live OpenAI/OpenClaw crawl.",
    ]
    return EvaluationRun(
        run_id=meta["run_id"],
        git_commit=meta["git_commit"],
        dataset_version=DATASET_VERSION,
        benchmark_version=BENCHMARK_VERSION,
        model=meta["model"],
        timestamp=meta["timestamp"],
        configuration=meta["configuration"],
        metrics=aggregate(scores),
        cases=scores,
        errors=errors,
        failures=failures,
        notes=notes,
    )


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="QuantPilot Phase 11 evaluation")
    parser.add_argument("--dataset", default="v1")
    parser.add_argument("--ablation", default="full", choices=ABLATIONS)
    parser.add_argument("--case-id", default="")
    parser.add_argument(
        "--out",
        default=str(ROOT / "evals" / "reports"),
        help="Directory for JSON/Markdown reports",
    )
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Also run the single-agent baseline and print a delta table",
    )
    parser.add_argument(
        "--all-ablations",
        action="store_true",
        help="Run full + component ablations and write one folder of reports",
    )
    args = parser.parse_args(argv)

    if args.all_ablations:
        for name in ABLATIONS:
            run = execute(dataset=args.dataset, ablation=name)
            paths = write_report(run, ROOT / "evals" / "reports" / name)
            print(f"{name}: cases={run.metrics.get('cases')} report={paths['md']}")
        return 0 if not run.errors else 1

    run = execute(dataset=args.dataset, ablation=args.ablation, case_id=args.case_id)
    paths = write_report(run, ROOT / "evals" / "reports")
    print(render_console(run))
    print(f"wrote {paths['md']}")
    if args.compare_baseline and args.ablation != "baseline":
        base = execute(dataset=args.dataset, ablation="baseline", case_id=args.case_id)
        write_report(base, ROOT / "evals" / "reports" / "baseline")
        print("\nBASELINE vs FULL (measured):")
        for key in (
            "citation_validity",
            "fabricated_citation_rate",
            "hallucination_rate",
            "hypothesis_recall",
            "primary_source_rate",
            "conclusion_label_match",
            "verification_recall",
        ):
            print(
                f"  {key}: baseline={base.metrics.get(key, 0):.3f} "
                f"full={run.metrics.get(key, 0):.3f}"
            )
    return 0 if not run.errors else 1


def render_console(run: EvaluationRun) -> str:
    m = run.metrics
    return "\n".join(
        [
            f"QUANTPILOT EVALUATION — {run.dataset_version}",
            f"commit {run.git_commit}  model {run.model}",
            f"Cases: {int(m.get('cases') or 0)}",
            f"Investigate P/R/F1: {m.get('investigate_precision', 0):.3f} / "
            f"{m.get('investigate_recall', 0):.3f} / {m.get('investigate_f1', 0):.3f}",
            f"Hypothesis Top-3: {m.get('top3_coverage', 0):.3f}",
            f"Primary source rate: {m.get('primary_source_rate', 0):.3f}",
            f"Citation validity: {m.get('citation_validity', 0):.3f}",
            f"Fabricated citation rate: {m.get('fabricated_citation_rate', 0):.3f}",
            f"Unsupported claim rate: {m.get('unsupported_claim_rate', 0):.3f}",
            f"Alert P/R: {m.get('alert_precision', 0):.3f} / {m.get('alert_recall', 0):.3f}",
            f"Avg tool calls: {m.get('avg_tool_calls', 0):.2f}",
            f"P95 latency (recorded): {m.get('latency_p95_ms', 0):.1f} ms",
            f"Failures: {len(run.failures)}  Errors: {len(run.errors)}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
