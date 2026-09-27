"""Deterministic name / ticker / CIK normalization. No fuzzy matching."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

_PUNCT = re.compile(r"[^\w\s&/+-]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_name(value: str) -> str:
    """Casefold, strip punctuation, collapse whitespace. Exact-match key only."""
    text = unicodedata.normalize("NFKC", (value or "").strip()).casefold()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_ticker(value: str) -> str:
    return (value or "").strip().upper()


def normalize_cik(value: str) -> str:
    text = (value or "").strip()
    if not text.isdigit() or len(text) > 10:
        raise ValueError(f"CIK must be up to 10 digits: {value!r}")
    return text.zfill(10)


def source_system_for_company(source: Optional[str]) -> str:
    raw = (source or "").strip().lower()
    if raw.startswith("sec"):
        return "sec"
    return raw or "sec"
