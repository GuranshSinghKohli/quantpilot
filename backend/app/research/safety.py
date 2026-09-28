"""Strip credential-shaped text before it is stored or returned."""

from __future__ import annotations

import re

_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer\s+\S+|sk-[a-z0-9]{8,}|password\s*[:=]\s*\S+|secret\s*[:=]\s*\S+)"
)


def sanitize(text: str, *, limit: int = 500) -> str:
    cleaned = _SECRET.sub("[redacted]", text or "")
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]
