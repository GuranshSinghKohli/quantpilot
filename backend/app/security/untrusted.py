"""Treat external webpage / filing / news text as data, never as instructions."""

from __future__ import annotations

import re

_INJECTION_RE = re.compile(
    r"(ignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts))"
    r"|(reveal\s+(your\s+)?(api\s*key|secrets?|credentials?))"
    r"|(system\s+prompt)"
    r"|(\boverride\s+safety\b)",
    re.I,
)

OMITTED = "[untrusted web instruction omitted]"


def sanitize_untrusted_text(text: str, max_chars: int = 8000) -> str:
    cleaned = []
    for line in (text or "").splitlines():
        if _INJECTION_RE.search(line):
            cleaned.append(OMITTED)
            continue
        cleaned.append(line)
    blob = "\n".join(cleaned)
    blob = _INJECTION_RE.sub(OMITTED, blob)
    return blob[:max_chars]


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))
