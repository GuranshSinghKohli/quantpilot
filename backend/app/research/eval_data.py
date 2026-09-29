"""Development research evaluation fixture. Not a production quality claim."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

NOW = datetime(2026, 4, 1, 15, 0, tzinfo=timezone.utc)

COMPANIES = [
    {"name": "Advanced Micro Devices Inc", "ticker": "AMD", "cik": "0000002488"},
    {"name": "Meta Platforms Inc", "ticker": "META", "cik": "0001326801"},
    {"name": "NVIDIA Corporation", "ticker": "NVDA", "cik": "0001045810"},
    {"name": "Apple Inc", "ticker": "AAPL", "cik": "0000320193"},
    {"name": "Microsoft Corporation", "ticker": "MSFT", "cik": "0000789019"},
]

DOCUMENTS = [
    {
        "title": "AMD and Meta announce AI infrastructure partnership",
        "summary": "AMD and Meta announced a partnership to deploy Instinct accelerators in Meta data centers.",
        "tickers": ["AMD", "META"],
    },
    {
        "title": "NVIDIA reports data-center GPU demand",
        "summary": "NVIDIA described continued demand for data-center GPUs used in large-scale training.",
        "tickers": ["NVDA"],
    },
    {
        "title": "Apple and Microsoft appear at the same industry conference",
        "summary": "Apple and Microsoft executives spoke on separate panels at the same conference. No deal was announced.",
        "tickers": ["AAPL", "MSFT"],
    },
    {
        "title": "Unrelated weather disrupts European airports",
        "summary": "Fog delayed flights across several European airports. No company technology was discussed.",
        "tickers": [],
    },
    {
        "title": "Microsoft develops custom AI accelerators",
        "summary": "Microsoft said it develops custom accelerators for its cloud, a documented DEVELOPS relationship in the graph.",
        "tickers": ["MSFT"],
    },
]

# Ground-truth is limited to fixture statements / expected types. Development only.
CASES: List[Dict[str, Any]] = [
    {
        "id": "amd-meta-partnership",
        "query": "AMD partnership with Meta",
        "tickers": ["AMD", "META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news", "company"],
    },
    {
        "id": "nvidia-gpu-demand",
        "query": "NVIDIA data-center GPU demand",
        "tickers": ["NVDA"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news", "technology"],
    },
    {
        "id": "apple-microsoft-conference",
        "query": "Apple Microsoft conference",
        "tickers": ["AAPL", "MSFT"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news"],
        "must_not_verify_relationship": True,
    },
    {
        "id": "unrelated-weather",
        "query": "European airport fog delays",
        "tickers": [],
        "expected_types": [],
        "must_cite": False,
        "scenario": "valid",
        "relevant_agents": ["news"],
        "allow_empty_specialists": True,
    },
    {
        "id": "microsoft-develops",
        "query": "Microsoft custom accelerators",
        "tickers": ["MSFT"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["company", "technology"],
    },
    {
        "id": "amd-company-identity",
        "query": "What is AMD",
        "tickers": ["AMD"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["company"],
    },
    {
        "id": "meta-company-identity",
        "query": "Meta Platforms canonical identity",
        "tickers": ["META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["company"],
    },
    {
        "id": "multi-entity-amd-meta",
        "query": "AMD Meta relationship",
        "tickers": ["AMD", "META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["company", "news"],
    },
    {
        "id": "technology-gpu",
        "query": "data-center GPU technology",
        "tickers": ["NVDA"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["technology", "news"],
    },
    {
        "id": "market-insufficient",
        "query": "AMD event window returns",
        "tickers": ["AMD"],
        "expected_types": [],
        "must_cite": False,
        "scenario": "valid",
        "relevant_agents": ["market"],
        "expect_no_market_observation": True,
    },
    {
        "id": "missing-citation-plant",
        "query": "AMD partnership with Meta",
        "tickers": ["AMD", "META"],
        "expected_types": [],
        "must_cite": True,
        "scenario": "missing_citation",
        "relevant_agents": ["news"],
        "expect_unsupported": True,
    },
    {
        "id": "unsupported-partnership-plant",
        "query": "Apple Microsoft conference",
        "tickers": ["AAPL", "MSFT"],
        "expected_types": [],
        "must_cite": True,
        "scenario": "unsupported",
        "relevant_agents": ["news"],
        "expect_unsupported": True,
    },
    {
        "id": "conflicting-acquisition-plant",
        "query": "AMD partnership with Meta",
        "tickers": ["AMD", "META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "conflicting",
        "relevant_agents": ["news"],
        "expect_conflict": True,
    },
    {
        "id": "empty-query-entity-only",
        "query": "canonical Microsoft company facts",
        "tickers": ["MSFT"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["company"],
    },
    {
        "id": "nvidia-news-event",
        "query": "NVIDIA GPU demand news",
        "tickers": ["NVDA"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news"],
    },
    {
        "id": "insufficient-unrelated",
        "query": "weather delays technology impact",
        "tickers": [],
        "expected_types": [],
        "must_cite": False,
        "scenario": "valid",
        "relevant_agents": ["technology"],
        "allow_empty_specialists": True,
    },
    {
        "id": "amd-news-only",
        "query": "AMD Instinct Meta data centers",
        "tickers": ["AMD"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news"],
    },
    {
        "id": "meta-news-only",
        "query": "Meta data center accelerators",
        "tickers": ["META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news"],
    },
    {
        "id": "partial-market-failure",
        "query": "AMD partnership with Meta",
        "tickers": ["AMD", "META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "fail_agents": ["news"],
        "relevant_agents": ["news", "company"],
        "expect_partial": True,
    },
    {
        "id": "all-specialists-news-company",
        "query": "AMD Meta announced partnership Instinct",
        "tickers": ["AMD", "META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["news", "company"],
    },
    {
        "id": "technology-no-use-inference",
        "query": "Apple Microsoft conference technology",
        "tickers": ["AAPL", "MSFT"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "valid",
        "relevant_agents": ["technology", "news"],
        "must_not_verify_relationship": True,
    },
    {
        "id": "high-confidence-unsupported",
        "query": "Apple Microsoft conference",
        "tickers": ["AAPL", "MSFT"],
        "expected_types": [],
        "must_cite": True,
        "scenario": "missing_citation",
        "relevant_agents": ["company"],
        "expect_unsupported": True,
    },
    {
        "id": "malformed-output",
        "query": "NVIDIA data-center GPU demand",
        "tickers": ["NVDA"],
        "expected_types": [],
        "must_cite": False,
        "scenario": "malformed",
        "fail_agents": None,
        "relevant_agents": ["news"],
        "expect_partial": True,
    },
    {
        "id": "transient-then-valid",
        "query": "AMD partnership with Meta",
        "tickers": ["AMD", "META"],
        "expected_types": ["FACT"],
        "must_cite": True,
        "scenario": "transient",
        "relevant_agents": ["news", "company"],
    },
]

# Case-type tags for the Phase 4.3/4.4 baseline. Adding a tag does not change scoring.
CASE_TYPES = {
    "amd-meta-partnership": "multi_agent",
    "nvidia-gpu-demand": "technology",
    "apple-microsoft-conference": "ambiguous_entity",
    "unrelated-weather": "no_evidence",
    "microsoft-develops": "company",
    "amd-company-identity": "company",
    "meta-company-identity": "company",
    "multi-entity-amd-meta": "multi_agent",
    "technology-gpu": "technology",
    "market-insufficient": "missing_market",
    "missing-citation-plant": "unsupported",
    "unsupported-partnership-plant": "unsupported",
    "conflicting-acquisition-plant": "conflict",
    "empty-query-entity-only": "company",
    "nvidia-news-event": "news",
    "insufficient-unrelated": "no_evidence",
    "amd-news-only": "news",
    "meta-news-only": "news",
    "partial-market-failure": "partial_failure",
    "all-specialists-news-company": "multi_agent",
    "technology-no-use-inference": "inference_scenario",
    "high-confidence-unsupported": "unsupported",
    "malformed-output": "partial_failure",
    "transient-then-valid": "async_replay",
}

# Repeated fixture queries. They widen coverage; they are not new ground truth.
_EXTRA_ROWS = [
    ("x-news-amd", "news", "AMD partnership with Meta", ["AMD", "META"], ["FACT"], ["news", "company"]),
    ("x-news-nvda", "news", "NVIDIA GPU demand news", ["NVDA"], ["FACT"], ["news"]),
    ("x-news-meta", "news", "Meta data center accelerators", ["META"], ["FACT"], ["news"]),
    ("x-company-amd", "company", "What is AMD", ["AMD"], ["FACT"], ["company"]),
    ("x-company-meta", "company", "Meta Platforms canonical identity", ["META"], ["FACT"], ["company"]),
    ("x-company-msft", "company", "canonical Microsoft company facts", ["MSFT"], ["FACT"], ["company"]),
    ("x-tech-gpu", "technology", "data-center GPU technology", ["NVDA"], ["FACT"], ["technology", "news"]),
    ("x-tech-accel", "technology", "Microsoft custom accelerators", ["MSFT"], ["FACT"], ["company", "technology"]),
    ("x-market-missing", "missing_market", "AMD event window returns", ["AMD"], [], ["market"]),
    ("x-market-volume", "market", "AMD event window returns", ["AMD"], [], ["market"]),
    ("x-multi-1", "multi_agent", "AMD Meta relationship", ["AMD", "META"], ["FACT"], ["company", "news"]),
    ("x-multi-2", "multi_agent", "AMD Meta announced partnership Instinct", ["AMD", "META"], ["FACT"], ["news", "company"]),
    ("x-none-weather", "no_evidence", "European airport fog delays", [], [], ["news"]),
    ("x-none-tech", "no_evidence", "weather delays technology impact", [], [], ["technology"]),
    ("x-ambig-1", "ambiguous_entity", "Apple Microsoft conference", ["AAPL", "MSFT"], ["FACT"], ["news"]),
    ("x-ambig-2", "ambiguous_entity", "Apple Microsoft conference technology", ["AAPL", "MSFT"], ["FACT"], ["technology", "news"]),
    ("x-conflict", "conflict", "AMD partnership with Meta", ["AMD", "META"], ["FACT"], ["news"]),
    ("x-partial-news", "partial_failure", "AMD partnership with Meta", ["AMD", "META"], ["FACT"], ["news", "company"]),
    ("x-partial-malformed", "partial_failure", "NVIDIA data-center GPU demand", ["NVDA"], [], ["news"]),
    ("x-unsupported-cite", "unsupported", "AMD partnership with Meta", ["AMD", "META"], [], ["news"]),
    ("x-unsupported-plant", "unsupported", "Apple Microsoft conference", ["AAPL", "MSFT"], [], ["news"]),
    ("x-infer", "inference_scenario", "Apple Microsoft conference technology", ["AAPL", "MSFT"], ["FACT"], ["technology", "news"]),
    ("x-dup-evidence", "duplicate_evidence", "AMD partnership with Meta", ["AMD", "META"], ["FACT"], ["news", "company"]),
    ("x-dup-doc", "duplicate_documents", "NVIDIA data-center GPU demand", ["NVDA"], ["FACT"], ["news"]),
    ("x-replay", "async_replay", "AMD partnership with Meta", ["AMD", "META"], ["FACT"], ["news", "company"]),
    ("x-idem-same", "idempotency", "AMD partnership with Meta", ["AMD", "META"], ["FACT"], ["news", "company"]),
]

_EXTRA_FLAGS = {
    "x-market-missing": {"expect_no_market_observation": True},
    "x-market-volume": {"expect_no_market_observation": True},
    "x-ambig-1": {"must_not_verify_relationship": True},
    "x-ambig-2": {"must_not_verify_relationship": True},
    "x-conflict": {"scenario": "conflicting", "expect_conflict": True},
    "x-partial-news": {"fail_agents": ["news"], "expect_partial": True},
    "x-partial-malformed": {"scenario": "malformed", "expect_partial": True},
    "x-unsupported-cite": {"scenario": "missing_citation", "expect_unsupported": True},
    "x-unsupported-plant": {"scenario": "unsupported", "expect_unsupported": True},
    "x-infer": {"must_not_verify_relationship": True},
    "x-none-weather": {"allow_empty_specialists": True},
    "x-none-tech": {"allow_empty_specialists": True},
    "x-replay": {"scenario": "transient"},
}


def extra_cases() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for case_id, case_type, query, tickers, expected, agents in _EXTRA_ROWS:
        row: Dict[str, Any] = {
            "id": case_id,
            "case_type": case_type,
            "query": query,
            "tickers": tickers,
            "expected_types": expected,
            "must_cite": bool(expected),
            "scenario": "valid",
            "relevant_agents": agents,
        }
        row.update(_EXTRA_FLAGS.get(case_id, {}))
        rows.append(row)
    return rows


# Corrected routing labels for the same queries. relevant_agents is unchanged
# so the Phase 4.5 unnecessary-agent rate stays comparable.
ROUTING_LABELS: Dict[str, Dict[str, List[str]]] = {
    "AMD partnership with Meta": {
        "required_agents": ["news", "company"],
        "optional_agents": [],
        "forbidden_agents": ["technology", "market"],
    },
    "NVIDIA data-center GPU demand": {
        "required_agents": ["technology"],
        "optional_agents": ["news"],
        "forbidden_agents": ["company", "market"],
    },
    "Apple Microsoft conference": {
        "required_agents": ["news"],
        "optional_agents": [],
        "forbidden_agents": ["company", "technology", "market"],
    },
    "European airport fog delays": {
        "required_agents": [],
        "optional_agents": [],
        "forbidden_agents": ["news", "company", "technology", "market"],
    },
    "Microsoft custom accelerators": {
        "required_agents": ["technology"],
        "optional_agents": ["company"],
        "forbidden_agents": ["news", "market"],
    },
    "What is AMD": {
        "required_agents": ["company"],
        "optional_agents": [],
        "forbidden_agents": ["news", "technology", "market"],
    },
    "Meta Platforms canonical identity": {
        "required_agents": ["company"],
        "optional_agents": [],
        "forbidden_agents": ["news", "technology", "market"],
    },
    "AMD Meta relationship": {
        "required_agents": ["company"],
        "optional_agents": ["news"],
        "forbidden_agents": ["technology", "market"],
    },
    "data-center GPU technology": {
        "required_agents": ["technology"],
        "optional_agents": ["news"],
        "forbidden_agents": ["company", "market"],
    },
    "AMD event window returns": {
        "required_agents": ["market"],
        "optional_agents": [],
        "forbidden_agents": ["news", "company", "technology"],
    },
    "canonical Microsoft company facts": {
        "required_agents": ["company"],
        "optional_agents": [],
        "forbidden_agents": ["news", "technology", "market"],
    },
    "NVIDIA GPU demand news": {
        "required_agents": ["news", "technology"],
        "optional_agents": [],
        "forbidden_agents": ["company", "market"],
    },
    "weather delays technology impact": {
        "required_agents": ["technology"],
        "optional_agents": [],
        "forbidden_agents": ["news", "company", "market"],
    },
    "AMD Instinct Meta data centers": {
        "required_agents": [],
        "optional_agents": ["news", "company", "technology"],
        "forbidden_agents": ["market"],
    },
    "Meta data center accelerators": {
        "required_agents": ["technology"],
        "optional_agents": ["news", "company"],
        "forbidden_agents": ["market"],
    },
    "AMD Meta announced partnership Instinct": {
        "required_agents": ["news", "company"],
        "optional_agents": ["technology"],
        "forbidden_agents": ["market"],
    },
    "Apple Microsoft conference technology": {
        "required_agents": ["news", "technology"],
        "optional_agents": [],
        "forbidden_agents": ["company", "market"],
    },
}


def all_cases() -> List[Dict[str, Any]]:
    tagged = []
    for case in CASES:
        row = dict(case)
        row["case_type"] = CASE_TYPES.get(str(case["id"]), "news")
        tagged.append(row)
    return tagged + extra_cases()
