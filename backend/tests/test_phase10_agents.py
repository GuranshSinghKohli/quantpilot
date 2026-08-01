"""Phase 10: Earnings, Macro, Verification agents + graph stream order."""

from app.agents.earnings_agent import _fallback as earnings_fallback
from app.agents.macro_agent import _fallback as macro_fallback
from app.agents.verification_agent import _fallback as verification_fallback
from app.evaluation.confidence import (
    score_earnings_agent,
    score_macro_agent,
    score_verification_agent,
)
from app.evaluation.validator import (
    ValidatedEarningsOutput,
    ValidatedMacroOutput,
    ValidatedVerificationOutput,
)
from app.workflows.analysis_graph import STREAM_AGENT_ORDER, STREAM_NODE_TO_AGENT


def test_stream_agent_order_includes_phase10():
    assert STREAM_AGENT_ORDER == [
        "news",
        "financial",
        "sec",
        "earnings",
        "macro",
        "risk",
        "bull",
        "bear",
        "verification",
        "report",
        "memo",
    ]
    assert STREAM_NODE_TO_AGENT["earnings_analysis"] == "earnings"
    assert STREAM_NODE_TO_AGENT["macro_analysis"] == "macro"
    assert STREAM_NODE_TO_AGENT["verification_analysis"] == "verification"


def test_earnings_fallback_shape():
    out = earnings_fallback(
        "AAPL",
        {"key_metrics": {"pe_ratio": 28.5}},
        {"filings": [{"form_type": "10-Q", "filing_date": "2024-01-01"}]},
        [{"title": "Apple beats EPS estimates"}],
    )
    assert out.earnings_summary
    assert out.key_points
    assert out.tone in ("positive", "mixed", "negative", "unknown")
    assert 0.0 <= out.confidence_score <= 1.0


def test_macro_fallback_shape():
    out = macro_fallback(
        "AAPL",
        [{"title": "Fed holds rates steady"}],
    )
    assert out.macro_summary
    assert out.relevance in ("high", "medium", "low", "none")
    assert isinstance(out.themes, list)


def test_verification_fallback_shape():
    out = verification_fallback(
        {"key_metrics": {"pe_ratio": 20}},
        {"filing_summary": "10-K risk factors noted."},
        {"sentiment": "bullish", "summary": "Positive news."},
        {"earnings_summary": "Solid quarter.", "key_points": ["EPS beat"]},
    )
    assert isinstance(out.verified_claims, list)
    assert 0.0 <= out.groundedness_score <= 1.0


def test_validated_earnings_defaults():
    v = ValidatedEarningsOutput.model_validate({})
    assert v.tone == "unknown"
    assert v.earnings_summary == "" or isinstance(v.earnings_summary, str)


def test_validated_macro_defaults():
    v = ValidatedMacroOutput.model_validate({})
    assert v.relevance in ("high", "medium", "low", "none")


def test_validated_verification_defaults():
    v = ValidatedVerificationOutput.model_validate({})
    assert 0.0 <= v.groundedness_score <= 1.0


def test_confidence_scorers():
    assert score_earnings_agent({"earnings_summary": "x" * 5, "key_points": [], "tone": "unknown"}) < 0.7
    assert score_macro_agent({"themes": [], "relevance": "none", "macro_summary": "short"}) < 0.65
    assert score_verification_agent({"groundedness_score": 0.9, "unsupported_claims": [], "verified_claims": ["ok"]}) >= 0.85
