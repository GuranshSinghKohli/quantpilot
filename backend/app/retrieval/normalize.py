"""Deterministic document text normalization. No LLM, no wording rewrite."""

from __future__ import annotations

import re
import unicodedata

_HTML_TAG = re.compile(r"<[^>]+>")
_MULTI_NL = re.compile(r"\n{3,}")
_HSPACE = re.compile(r"[ \t\f\v]+")


def normalize_document_text(text: str) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFC", text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _HTML_TAG.sub(" ", value)
    lines = [_HSPACE.sub(" ", line).strip() for line in value.split("\n")]
    value = "\n".join(lines)
    value = _MULTI_NL.sub("\n\n", value)
    return value.strip()


def is_usable_text(text: str) -> bool:
    return any(ch.isalnum() for ch in text)
