"""The arena refuses to start a cycle when the model id is the server default."""

from __future__ import annotations

import pytest

from app.league.arena import UnconfiguredModel, require_configured_model


def test_unconfigured_model_is_refused() -> None:
    with pytest.raises(UnconfiguredModel):
        require_configured_model("unconfigured")


def test_blank_model_is_refused() -> None:
    with pytest.raises(UnconfiguredModel):
        require_configured_model("")
    with pytest.raises(UnconfiguredModel):
        require_configured_model("   ")


def test_a_named_model_is_returned() -> None:
    assert require_configured_model("  openrouter/meta-llama ") == "openrouter/meta-llama"
