"""Arena run gate. The scheduler calls this before a cycle. There is no cycle yet."""

from __future__ import annotations

UNCONFIGURED_MODEL = "unconfigured"


class UnconfiguredModel(Exception):
    """The roster row has no model id. The arena does not call a provider."""


def require_configured_model(llm_model: str) -> str:
    """Return the model id. Refuse the server default and a blank id."""
    model = "" if llm_model is None else str(llm_model).strip()
    if model == "" or model == UNCONFIGURED_MODEL:
        raise UnconfiguredModel("llm_model is unconfigured")
    return model
