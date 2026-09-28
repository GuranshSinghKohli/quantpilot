"""FakeLLMProvider scenarios. No external API calls."""

from __future__ import annotations

import pytest
from app.research.llm import FakeLLMProvider, LLMError, OpenAILLMProvider
from app.research.schemas import AgentFindingsPayload


def _gen(provider, agent="news", context=None):
    return provider.generate_structured(
        system="sys",
        user="user",
        schema=AgentFindingsPayload,
        prompt_name=agent,
        prompt_version="news-v1",
        context=context or {"citation_ids": ["c1", "c2"], "documents": []},
    )


def test_same_input_same_findings():
    provider = FakeLLMProvider()
    context = {
        "documents": [{"title": "AMD news", "excerpt": "AMD announced chips", "citation_ids": ["c1"]}],
        "citation_ids": ["c1"],
    }
    a = _gen(provider, context=context)
    b = _gen(FakeLLMProvider(), context=context)
    assert a.data.findings[0].finding_id == b.data.findings[0].finding_id
    assert a.data.findings[0].statement == b.data.findings[0].statement


def test_missing_citation_and_unsupported_and_conflict():
    missing = _gen(FakeLLMProvider(scenario="missing_citation"))
    assert missing.data.findings[0].citations == []
    assert missing.data.findings[0].confidence == 0.99
    unsupported = _gen(FakeLLMProvider(scenario="unsupported"))
    assert unsupported.data.findings[0].statement == "Meta partnered with AMD."
    conflict = _gen(FakeLLMProvider(scenario="conflicting"))
    assert len(conflict.data.findings) == 2
    assert "did not" in conflict.data.findings[1].statement


def test_malformed_and_failures():
    with pytest.raises(LLMError) as malformed:
        _gen(FakeLLMProvider(scenario="malformed"))
    assert malformed.value.error_type == "malformed"
    with pytest.raises(LLMError) as transient:
        _gen(FakeLLMProvider(scenario="transient", fail_times=1))
    assert transient.value.retryable is True
    with pytest.raises(LLMError) as rate:
        _gen(FakeLLMProvider(scenario="rate_limit"))
    assert rate.value.error_type == "rate_limit"
    with pytest.raises(LLMError) as timeout:
        _gen(FakeLLMProvider(scenario="timeout"))
    assert timeout.value.error_type == "timeout"
    with pytest.raises(LLMError) as perm:
        _gen(FakeLLMProvider(scenario="permanent"))
    assert perm.value.retryable is False
    assert perm.value.error_type == "auth"


def test_transient_then_succeeds():
    provider = FakeLLMProvider(scenario="transient", fail_times=1)
    with pytest.raises(LLMError):
        _gen(provider)
    ok = _gen(provider)
    assert isinstance(ok.data, AgentFindingsPayload)


def test_openai_provider_requires_key(monkeypatch):
    from app.core.settings import Settings

    monkeypatch.setenv("OPENAI_API_KEY", "")
    # Constructing with empty key must fail closed without a network call.
    settings = Settings(OPENAI_API_KEY="")
    monkeypatch.setattr("app.research.llm.get_settings", lambda: settings)
    with pytest.raises(LLMError) as exc:
        OpenAILLMProvider()
    assert exc.value.error_type == "auth"
