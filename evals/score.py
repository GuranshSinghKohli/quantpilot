"""Score a replay against ground truth. Truth stays in the evaluator."""

from __future__ import annotations

from typing import Any, Dict

from evals.metrics.advocate import advocate_metrics
from evals.metrics.citations import citation_metrics
from evals.metrics.conclusion import conclusion_metrics
from evals.metrics.evidence import evidence_metrics
from evals.metrics.hallucination import hallucination_metrics
from evals.metrics.hypotheses import score_hypotheses
from evals.metrics.memory import memory_metrics
from evals.metrics.tools import tool_metrics
from evals.metrics.verification import verification_metrics
from evals.schemas import CaseScore, PackedCase
from evals.temporal import parse_time


def _materiality_label(should: bool, score: float) -> str:
    if not should:
        return "LOW_MATERIALITY"
    if score >= 70:
        return "HIGH_MATERIALITY"
    return "MEDIUM_MATERIALITY"


def score_replay(packed: PackedCase, actual: Dict[str, Any]) -> CaseScore:
    truth = packed.truth
    cutoff = parse_time(packed.case.cutoff_time)
    statements = actual.get("hypotheses") or []
    evidence = actual.get("evidence") or []
    failures = []
    flags = []

    detected_types = set(actual.get("events") or [])
    expected_types = set(truth.expected_event_types)
    type_hit = bool(detected_types & expected_types) if expected_types else bool(detected_types)
    if truth.should_detect and not type_hit:
        failures.append(
            {
                "type": "EVENT_DETECTION",
                "expected": list(expected_types),
                "actual": list(detected_types),
            }
        )
        flags.append("EVENT_DETECTION")

    should_inv = bool(actual.get("should_investigate_materiality"))
    if should_inv != truth.should_investigate:
        failures.append(
            {
                "type": "MATERIALITY",
                "expected": truth.should_investigate,
                "actual": should_inv,
            }
        )
        flags.append("MATERIALITY")

    hyp = score_hypotheses(
        statements,
        truth.acceptable_hypothesis_phrases,
        truth.unacceptable_hypothesis_phrases,
    )
    if truth.acceptable_hypothesis_phrases and hyp["hypothesis_recall"] < 1:
        flags.append("HYPOTHESIS")
        failures.append(
            {
                "type": "HYPOTHESIS",
                "expected": truth.acceptable_hypothesis_phrases,
                "actual": statements,
            }
        )

    ev = evidence_metrics(evidence, statements, truth.known_contradictions)
    cite = citation_metrics(
        actual.get("hypothesis_models") or packed.fixture.hypotheses,
        evidence,
        cutoff,
        allow_retrospective=packed.case.allow_retrospective,
    )
    if cite["fabricated_citation_rate"] > 0:
        flags.append("EVIDENCE")
        failures.append(
            {
                "type": "EVIDENCE",
                "expected": "no fabricated citations",
                "actual": cite["fabricated_citation_rate"],
            }
        )

    expected_uncertain = any(
        s in {"UNCERTAIN", "INSUFFICIENT_EVIDENCE"}
        for s in truth.expected_conclusion_strengths
    ) and not any(
        s in {"SUPPORTED", "ROBUST"} for s in truth.expected_conclusion_strengths
    )
    hall = hallucination_metrics(
        claim_statuses=actual.get("claim_statuses") or [],
        fabricated_citation_rate=cite["fabricated_citation_rate"],
        expected_uncertain=expected_uncertain,
        conclusion_strength=str(actual.get("conclusion_strength") or ""),
    )
    ver = verification_metrics(
        truth.planted_flaws,
        actual.get("verification_issues") or [],
        flagged_unsupported=bool(actual.get("flagged_unsupported")),
        flagged_temporal=bool(actual.get("flagged_temporal")),
    )
    if truth.planted_flaws and ver["verification_recall"] < 1:
        flags.append("VERIFICATION")
        failures.append(
            {
                "type": "VERIFICATION",
                "expected": truth.planted_flaws,
                "actual": actual.get("verification_issues"),
            }
        )

    da = advocate_metrics(
        str(actual.get("da_counter") or ""),
        truth.known_contradictions,
        str(actual.get("da_alternative") or ""),
        actual.get("da_needed") or [],
        challenged_causal=bool(actual.get("da_challenged_causal")),
    )
    conc = conclusion_metrics(
        str(actual.get("conclusion_strength") or ""),
        truth.expected_conclusion_strengths,
    )
    if truth.expected_conclusion_strengths and conc["conclusion_label_match"] < 1:
        flags.append("SYNTHESIS")
        failures.append(
            {
                "type": "SYNTHESIS",
                "expected": truth.expected_conclusion_strengths,
                "actual": actual.get("conclusion_strength"),
            }
        )

    mem = memory_metrics(
        (actual.get("memory") or {}).get("statements") or [],
        [p for p in truth.acceptable_hypothesis_phrases if packed.case.track == "company"],
        expected_change=truth.expected_memory_change,
        detected_change=bool((actual.get("memory") or {}).get("trend")),
    )
    if packed.case.track == "company" and truth.expected_memory_change and mem["memory_conflict_detection"] < 1:
        flags.append("MEMORY")

    tools = tool_metrics(
        actual.get("tool_trace") or packed.fixture.tool_trace,
        used_browser=bool(actual.get("used_browser")),
        should_use_browser=truth.should_use_browser,
    )
    if truth.should_use_browser is not None and tools["tool_routing_accuracy"] < 1:
        flags.append("TOOL_ROUTING")
        failures.append(
            {
                "type": "TOOL_ROUTING",
                "expected": truth.should_use_browser,
                "actual": actual.get("used_browser"),
            }
        )
    if truth.expected_browser_domain:
        domain = str(actual.get("browser_domain") or "")
        if truth.expected_browser_domain not in domain:
            flags.append("BROWSER")
            failures.append(
                {
                    "type": "BROWSER",
                    "expected": truth.expected_browser_domain,
                    "actual": domain,
                }
            )

    notify_ok = 1.0 if bool(actual.get("should_notify")) == truth.should_notify else 0.0
    if notify_ok < 1 and packed.case.track == "alert":
        flags.append("ALERT")
        failures.append(
            {
                "type": "ALERT",
                "expected": truth.should_notify,
                "actual": actual.get("should_notify"),
            }
        )

    mat_label = _materiality_label(should_inv, float(actual.get("materiality_score") or 0))
    mat_match = 1.0 if mat_label == truth.expected_materiality else 0.0
    # HIGH vs MEDIUM both investigate — treat adjacent labels as partial.
    if mat_match == 0 and should_inv == truth.should_investigate:
        if {mat_label, truth.expected_materiality} <= {
            "HIGH_MATERIALITY",
            "MEDIUM_MATERIALITY",
        }:
            mat_match = 0.5

    metrics = {
        "event_type_hit": 1.0 if type_hit else 0.0,
        "investigate_match": 1.0 if should_inv == truth.should_investigate else 0.0,
        "materiality_label_match": mat_match,
        "notify_match": notify_ok,
        **hyp,
        **ev,
        **cite,
        **hall,
        **ver,
        **da,
        **conc,
        **mem,
        **tools,
        "llm_calls": float(actual.get("llm_calls") or 0),
        "llm_tokens": float(actual.get("llm_tokens") or 0),
    }
    return CaseScore(
        case_id=packed.case.case_id,
        track=packed.case.track,
        metrics=metrics,
        flags=flags,
        failures=failures,
        actual={
            "events": actual.get("events"),
            "should_investigate": should_inv,
            "materiality_score": actual.get("materiality_score"),
            "materiality_label": mat_label,
            "should_notify": actual.get("should_notify"),
            "conclusion_strength": actual.get("conclusion_strength"),
            "memory_trend": (actual.get("memory") or {}).get("trend"),
        },
        latency_ms=actual.get("latency_ms") or {},
        tool_calls=int(tools.get("tool_calls") or 0),
        sources_used=[e.source_url for e in evidence if e.source_url],
        hypotheses=statements,
        conclusion=str(actual.get("conclusion") or ""),
    )
