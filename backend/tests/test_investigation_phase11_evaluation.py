"""Phase 11 — evaluation framework: dataset, cutoff, metrics, runner, isolation."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QUANTPILOT_LIGHTWEIGHT", "true")
os.environ.setdefault("INVESTIGATIONS_SWEEP_ENABLED", "false")

from evals.dataset import DatasetError, load_dataset, public_case_view, validate_case
from evals.datasets.catalog import cases_by_id, load_v1
from evals.isolation import isolated_db
from evals.metrics.citations import citation_metrics
from evals.metrics.classification import f1, precision, recall
from evals.metrics.hallucination import hallucination_metrics
from evals.replay import replay_case
from evals.reproduce import git_commit, run_metadata
from evals.run import execute
from evals.schemas import (
    EvaluationCase,
    EvidenceFixture,
    GroundTruth,
    HypothesisFixture,
    PackedCase,
    ReplayFixture,
)
from evals.temporal import filter_evidence, parse_time


def test_dataset_loads_and_validates():
    packs = load_dataset("v1")
    assert len(packs) >= 20
    ids = [p.case.case_id for p in packs]
    assert len(ids) == len(set(ids))
    for pack in packs:
        assert pack.case.cutoff_time
        assert parse_time(pack.case.cutoff_time) is not None


def test_invalid_case_missing_cutoff():
    bad = PackedCase(
        case=EvaluationCase(
            case_id="bad",
            ticker="X",
            event_timestamp="2024-01-01T00:00:00+00:00",
            cutoff_time="",
            event_type="PRICE_MOVE",
            event_description="x",
        ),
        truth=GroundTruth(),
        fixture=ReplayFixture(),
    )
    errs = validate_case(bad)
    assert any("cutoff" in e for e in errs)


def test_malformed_materiality_rejected():
    try:
        GroundTruth(expected_materiality="YES")
        assert False, "should reject"
    except Exception:
        pass


def test_temporal_filter_drops_future_sources():
    cutoff = parse_time("2024-01-24T23:59:59+00:00")
    items = [
        EvidenceFixture(
            title="same day",
            source_url="https://investor.nvidia.com/a",
            published_at="2024-01-24T20:00:00+00:00",
            source_type="ir_page",
        ),
        EvidenceFixture(
            title="week later recap",
            source_url="https://www.example-news.test/later",
            published_at="2024-01-30T12:00:00+00:00",
            source_type="news",
        ),
    ]
    kept = filter_evidence(items, cutoff)
    assert [k.title for k in kept] == ["same day"]


def test_hard_case_drops_post_event_recap_before_production():
    pack = cases_by_id()["nvda-2024-01-24-hard"]
    actual = replay_case(pack)
    urls = [e.source_url for e in actual["evidence"]]
    assert "https://www.example-news.test/nvda-recap-jan-30" not in urls
    assert "truth" not in actual


def test_ground_truth_not_in_public_view():
    pack = load_v1()[0]
    public = public_case_view(pack)
    assert "acceptable_hypothesis_phrases" not in public
    assert "expected_materiality" not in public
    blob = str(public)
    assert "Expected answer" not in blob


def test_metrics_precision_recall_f1():
    assert precision(3, 1) == 0.75
    assert recall(3, 1) == 0.75
    assert abs(f1(0.75, 0.75) - 0.75) < 1e-9
    hall = hallucination_metrics(
        claim_statuses=["SUPPORTED", "UNSUPPORTED"],
        fabricated_citation_rate=0.0,
        expected_uncertain=True,
        conclusion_strength="INSUFFICIENT_EVIDENCE",
    )
    assert hall["unsupported_claim_rate"] == 0.5
    assert hall["appropriate_uncertainty_rate"] == 1.0


def test_citation_validity_and_fabrication():
    cutoff = datetime(2024, 1, 24, tzinfo=timezone.utc)
    ev = [
        EvidenceFixture(
            title="IR",
            source_url="https://investor.nvidia.com/q",
            published_at="2024-01-22T00:00:00+00:00",
            source_type="ir_page",
        )
    ]
    hyps = [
        HypothesisFixture(
            statement="guidance",
            citations=["https://investor.nvidia.com/q"],
            evidence_indices=[0],
        ),
        HypothesisFixture(
            statement="secret",
            citations=["https://not-a-real-source.invalid/secret"],
        ),
    ]
    m = citation_metrics(hyps, ev, cutoff)
    assert m["fabricated_citation_rate"] > 0
    assert 0 < m["citation_validity"] < 1


def test_alert_precision_on_labeled_cases():
    noise = replay_case(cases_by_id()["alert-noise-small-move"])
    material = replay_case(cases_by_id()["alert-material-nvda"])
    assert noise["should_investigate_materiality"] is False
    assert material["should_investigate_materiality"] is True


def test_isolated_db_does_not_use_production_url():
    before = os.environ.get("DATABASE_URL")
    with isolated_db() as db:
        db.execute(__import__("sqlalchemy").text("select 1"))
    assert os.environ.get("DATABASE_URL") == before


def test_reproducibility_fields():
    meta = run_metadata({"dataset": "v1"})
    assert meta["dataset_version"] == "v1"
    assert meta["git_commit"]
    assert meta["run_id"]
    assert meta["model"]
    assert git_commit()


def test_runner_executes_and_writes_identity():
    run = execute(dataset="v1", case_id="spy-2022-09-13-cpi")
    assert run.git_commit
    assert run.dataset_version == "v1"
    assert run.configuration["isolated_db"] is True
    assert run.cases[0].case_id == "spy-2022-09-13-cpi"
    assert run.cases[0].actual["should_investigate"] is False


def test_full_v1_run_is_isolated_and_measured():
    run = execute(dataset="v1", ablation="full")
    assert not run.errors, run.errors
    assert int(run.metrics["cases"]) == len(load_v1())
    assert "investigate_f1" in run.metrics
    assert "fabricated_citation_rate" in run.metrics
    assert "hallucination_rate" in run.metrics
    assert "alert_precision" in run.metrics
    # No hardcoded showcase numbers — only that they were computed.
    assert 0.0 <= run.metrics["investigate_f1"] <= 1.0
    assert run.metrics["fabricated_citation_rate"] >= 0.0
