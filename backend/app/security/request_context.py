"""Correlation identifiers for a single HTTP request / investigation."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_investigation_id: ContextVar[str] = ContextVar("investigation_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(value: str) -> None:
    _request_id.set(value or "")


def get_request_id() -> str:
    return _request_id.get() or ""


def set_investigation_id(value: Optional[int]) -> None:
    _investigation_id.set(str(value) if value is not None else "")


def get_investigation_id() -> str:
    return _investigation_id.get() or ""


def correlation_fields() -> dict:
    out = {}
    rid = get_request_id()
    iid = get_investigation_id()
    if rid:
        out["request_id"] = rid
    if iid:
        out["investigation_id"] = iid
    return out
