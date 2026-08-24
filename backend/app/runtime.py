"""Process-level runtime flags used during graceful shutdown."""

from __future__ import annotations

_draining = False


def set_draining(value: bool) -> None:
    global _draining
    _draining = bool(value)


def is_draining() -> bool:
    return _draining
