"""Check eval/human_claim_audit_v1.json and print the manual-audit counts.

This does not import the research pipeline and does not relabel claims.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "eval" / "human_claim_audit_v1.json"

FACTUAL = {"CORRECT", "PARTIALLY_CORRECT", "INCORRECT", "NOT_VERIFIABLE"}
SUPPORT = {"FULLY_SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "SOURCE_UNAVAILABLE"}
CITATION = {"CORRECT_CITATION", "WRONG_CITATION", "INSUFFICIENT_CITATION", "NO_CITATION_REQUIRED"}
ENTITY = {"CORRECT_ENTITY", "WRONG_ENTITY", "AMBIGUOUS"}
MARKET = {"CORRECT_SECURITY", "WRONG_SECURITY", "NOT_APPLICABLE"}
CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
STATUS = {"supported", "uncertain", "unsupported", "conflicting"}
KIND = {"FACT", "OBSERVATION", "INFERENCE", "SCENARIO"}
ERRORS = {
    "wrong_entity",
    "wrong_security",
    "unsupported_claim",
    "overclaim",
    "citation_mismatch",
    "source_ambiguity",
    "relationship_inference",
    "market_calculation",
    "temporal_mismatch",
    "stale_source",
    "incomplete_context",
    "other",
}
SECRET_MARKERS = ("sk-", "AKIA", "BEGIN PRIVATE", "api_key", "password=")


def load() -> dict:
    doc = json.loads(PATH.read_text())
    text = PATH.read_text()
    for marker in SECRET_MARKERS:
        if marker in text:
            raise SystemExit(f"secret-like marker {marker!r} in {PATH}")
    cases = doc["cases"]
    if doc.get("dataset_version") != "human-claim-audit-v1":
        raise SystemExit("unexpected dataset_version")
    if len(cases) < 30:
        raise SystemExit(f"expected about 30 cases, found {len(cases)}")
    ids = [c["evaluation_id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit("evaluation_id values are not unique")
    required = [
        "evaluation_id",
        "research_id",
        "claim",
        "finding_type",
        "quantpilot_status",
        "citation_ids",
        "source_urls",
        "human_label",
        "human_support_level",
        "citation_correctness",
        "entity_correctness",
        "market_correctness",
        "human_confidence",
        "human_reason",
        "error_types",
    ]
    for case in cases:
        missing = [key for key in required if case.get(key) in (None, "")]
        if missing:
            raise SystemExit(f"{case.get('evaluation_id')} missing {missing}")
        if case["human_label"] not in FACTUAL:
            raise SystemExit(case["evaluation_id"])
        if case["human_support_level"] not in SUPPORT:
            raise SystemExit(case["evaluation_id"])
        if case["citation_correctness"] not in CITATION:
            raise SystemExit(case["evaluation_id"])
        if case["entity_correctness"] not in ENTITY:
            raise SystemExit(case["evaluation_id"])
        if case["market_correctness"] not in MARKET:
            raise SystemExit(case["evaluation_id"])
        if case["human_confidence"] not in CONFIDENCE:
            raise SystemExit(case["evaluation_id"])
        if case["quantpilot_status"] not in STATUS:
            raise SystemExit(case["evaluation_id"])
        if case["finding_type"] not in KIND:
            raise SystemExit(case["evaluation_id"])
        unknown = [item for item in case["error_types"] if item not in ERRORS]
        if unknown:
            raise SystemExit(f"{case['evaluation_id']} unknown errors {unknown}")
        if not case["human_reason"]:
            raise SystemExit(case["evaluation_id"])
    return doc


def gold_supported(case: dict) -> bool:
    """A factual or observation claim the source fully supports."""
    return (
        case["finding_type"] in {"FACT", "OBSERVATION"}
        and case["human_label"] == "CORRECT"
        and case["human_support_level"] == "FULLY_SUPPORTED"
    )


def strict_status_agreement(case: dict) -> bool:
    status = case["quantpilot_status"]
    if status == "supported":
        return case["human_label"] == "CORRECT" and case["human_support_level"] == "FULLY_SUPPORTED"
    if status == "uncertain":
        return case["finding_type"] in {"INFERENCE", "SCENARIO"} and case["human_label"] != "INCORRECT" and case["human_support_level"] != "NOT_SUPPORTED"
    if status == "unsupported":
        return case["human_support_level"] == "NOT_SUPPORTED" or case["human_label"] == "INCORRECT"
    return False


def metrics(doc: dict) -> dict:
    cases = doc["cases"]
    n = len(cases)
    factual = Counter(c["human_label"] for c in cases)
    support = Counter(c["human_support_level"] for c in cases)
    citation = Counter(c["citation_correctness"] for c in cases)
    entity = Counter(c["entity_correctness"] for c in cases)
    market_cases = [c for c in cases if c["market_correctness"] != "NOT_APPLICABLE"]
    market = Counter(c["market_correctness"] for c in market_cases)
    status = Counter(c["quantpilot_status"] for c in cases)
    matrix = Counter((c["quantpilot_status"], c["human_label"]) for c in cases)
    primary = Counter((c["error_types"][0] if c["error_types"] else "none") for c in cases)
    any_error = Counter(err for c in cases for err in c["error_types"])
    gold = [c for c in cases if gold_supported(c)]
    predicted = [c for c in cases if c["quantpilot_status"] == "supported"]
    tp = [c for c in predicted if gold_supported(c)]
    fp = [c for c in predicted if not gold_supported(c)]
    fn = [c for c in gold if c["quantpilot_status"] != "supported"]
    precision = len(tp) / len(predicted) if predicted else 0
    recall = len(tp) / len(gold) if gold else 0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0
    return {
        "n": n,
        "factual": factual,
        "support": support,
        "citation": citation,
        "entity": entity,
        "market_n": len(market_cases),
        "market": market,
        "status": status,
        "matrix": matrix,
        "primary": primary,
        "any_error": any_error,
        "tp": len(tp),
        "fp": len(fp),
        "fn": len(fn),
        "gold": len(gold),
        "predicted": len(predicted),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "strict_agree": sum(strict_status_agreement(c) for c in cases),
        "reports": len({c["research_id"] for c in cases}),
    }


def main() -> int:
    doc = load()
    m = metrics(doc)
    print(f"ok {PATH.name} n={m['n']} reports={m['reports']}")
    print("factual", dict(m["factual"]))
    print("support", dict(m["support"]))
    print("citation", dict(m["citation"]))
    print("entity", dict(m["entity"]))
    print("market", m["market_n"], dict(m["market"]))
    print("status", dict(m["status"]))
    print("matrix")
    for status in ("supported", "uncertain", "unsupported", "conflicting"):
        row = [m["matrix"][(status, label)] for label in ("CORRECT", "PARTIALLY_CORRECT", "INCORRECT", "NOT_VERIFIABLE")]
        print(f"  {status:12} {row}")
    print("primary", dict(m["primary"]))
    print("any_error", dict(m["any_error"]))
    print(
        f"precision {m['tp']}/{m['predicted']} recall {m['tp']}/{m['gold']} "
        f"fn {m['fn']} f1 {m['f1']:.4f} strict_agree {m['strict_agree']}/{m['n']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
