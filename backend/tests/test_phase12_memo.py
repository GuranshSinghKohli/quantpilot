"""Phase 12: Investment Memo agent + graph stream order + observability bootstrap."""

from app.agents.investment_memo_agent import _fallback as memo_fallback
from app.evaluation.confidence import score_memo_agent
from app.evaluation.validator import ValidatedInvestmentMemo, validate_memo
from app.observability.instrumentation import (
    configure_langsmith,
    configure_opentelemetry,
    configure_sentry,
    init_observability,
)
from app.workflows.analysis_graph import STREAM_AGENT_ORDER, STREAM_NODE_TO_AGENT


def test_stream_agent_order_includes_memo():
    assert STREAM_AGENT_ORDER[-1] == "memo"
    assert "memo" in STREAM_AGENT_ORDER
    assert STREAM_AGENT_ORDER.index("report") < STREAM_AGENT_ORDER.index("memo")
    assert STREAM_NODE_TO_AGENT["investment_memo"] == "memo"
    assert STREAM_NODE_TO_AGENT["final_report"] == "report"


def test_memo_fallback_shape():
    out = memo_fallback(
        "NVDA",
        {
            "executive_summary": "Strong AI demand with elevated valuation.",
            "recommendation": "Hold with caution.",
        },
        risk_output={"risk_factors": ["Valuation risk", "Competition"]},
        debate_output={
            "bull": {"thesis": "AI cycle continues."},
            "bear": {"thesis": "Multiple compression."},
        },
        metrics_output={"key_metrics": {"pe_ratio": 45.0, "current_price": 120.0}},
    )
    assert out.ticker == "NVDA"
    assert out.decision in ("BUY", "HOLD", "SELL", "WATCH")
    assert out.conviction in ("low", "medium", "high")
    assert out.key_numbers
    assert out.risks
    assert 0.0 <= out.confidence_score <= 1.0


def test_validate_memo_defaults():
    validated, warnings = validate_memo({}, "AAPL")
    assert validated["ticker"] == "AAPL"
    assert validated["decision"] in ("BUY", "HOLD", "SELL", "WATCH")
    assert validated["conviction"] in ("low", "medium", "high")
    assert validated["investment_thesis"]


def test_validated_memo_model():
    v = ValidatedInvestmentMemo.model_validate({"ticker": "MSFT"})
    assert v.decision == "HOLD"
    assert v.conviction == "medium"


def test_score_memo_agent():
    high = score_memo_agent(
        {
            "investment_thesis": " ".join(["word"] * 40),
            "key_numbers": ["P/E 20"],
            "risks": ["Competition"],
            "decision": "HOLD",
        },
        report_confidence=0.9,
    )
    low = score_memo_agent(
        {"investment_thesis": "short", "decision": "MAYBE"},
        report_confidence=0.9,
    )
    assert high > low
    assert 0.0 <= low <= 1.0


def test_observability_noop_without_keys(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("OTEL_ENABLED", "false")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert configure_langsmith() is False
    assert configure_sentry() is False
    assert configure_opentelemetry() is False
    status = init_observability()
    assert status == {"langsmith": False, "sentry": False, "otel": False}


def test_langsmith_enables_when_configured(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "quantpilot-ci")
    assert configure_langsmith() is True
