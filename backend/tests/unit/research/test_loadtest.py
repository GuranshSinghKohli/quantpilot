"""Pure checks for the research load harness. No broker and no database."""

import pytest

from app.research.loadtest import (
    DEFAULT_WEIGHTS,
    LoadTargetError,
    allocate,
    assert_disposable_url,
    build_workload,
    nearest_rank,
    percentiles,
)


def test_nearest_rank_is_the_same_method_for_each_percentile():
    samples = list(range(1, 101))
    assert nearest_rank(samples, 50) == 50
    assert nearest_rank(samples, 95) == 95
    assert nearest_rank(samples, 99) == 99
    assert nearest_rank([], 50) is None


def test_percentiles_flag_a_small_sample():
    result = percentiles([1, 2, 3])
    assert result["method"] == "nearest-rank"
    assert result["p50"] == nearest_rank([1, 2, 3], 50)
    assert result["p95"] == nearest_rank([1, 2, 3], 95)
    assert result["p99"] == nearest_rank([1, 2, 3], 99)
    assert any("below 100" in note for note in result["notes"])
    assert any("below 20" in note for note in result["notes"])


def test_workload_is_deterministic_and_mixed():
    first = build_workload(100)
    second = build_workload(100)
    assert first == second
    counts = {}
    for item in first:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    assert counts == allocate(100, DEFAULT_WEIGHTS)
    assert sum(counts.values()) == 100
    assert counts["news"] == 20
    assert counts["partial-agent"] == 2
    assert first[0]["kind"] != first[1]["kind"]
    queries = [item["query"] for item in first if item["kind"] == "news"]
    assert len(set(queries)) > 1


def test_disposable_url_refuses_developer_databases():
    with pytest.raises(LoadTargetError):
        assert_disposable_url("sqlite:////Users/guranshkohli/quantpilot/backend/data/quantpilot.db")
    with pytest.raises(LoadTargetError):
        assert_disposable_url("postgresql://quantpilot:quantpilot@localhost:5432/quantpilot")
    with pytest.raises(LoadTargetError):
        assert_disposable_url("postgresql://quantpilot:quantpilot@localhost:55432/quantpilot_test")
    assert_disposable_url("postgresql://quantpilot:quantpilot@localhost:55432/quantpilot_load")
