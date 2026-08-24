"""Redact credentials before they reach logs or error payloads."""

from __future__ import annotations

from typing import Any, Dict

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "gateway_token",
    "jwt",
    "openai_api_key",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}


def is_sensitive_key(key: str) -> bool:
    lowered = (key or "").strip().lower()
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(part in lowered for part in ("password", "secret", "token", "api_key", "cookie"))


def redact_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    text = str(value)
    if len(text) >= 8 and text.startswith(("sk-", "lsv2_", "Bearer ")):
        return "[redacted]"
    return value


def redact_mapping(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if is_sensitive_key(str(key)):
            out[key] = "[redacted]"
        else:
            out[key] = redact_value(value)
    return out
