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


# Closed suffix list. Used only to derive a display stem from an authoritative legal name.
_LEGAL_SUFFIXES = (
    "incorporated",
    "corporation",
    "company",
    "limited",
    "inc",
    "corp",
    "ltd",
    "llc",
    "plc",
    "co",
)


def legal_name_stem(value: str) -> Optional[str]:
    """Strip a trailing legal suffix. 'NVIDIA CORPORATION' → 'NVIDIA'. Not fuzzy merge."""
    raw = (value or "").strip()
    if not raw:
        return None
    tokens = raw.split()
    if len(tokens) < 2:
        return None
    last = _PUNCT.sub("", tokens[-1]).casefold().rstrip(".")
    if last not in _LEGAL_SUFFIXES:
        return None
    stem = " ".join(tokens[:-1]).rstrip(",").strip()
    return stem or None
