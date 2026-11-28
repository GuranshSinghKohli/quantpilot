"""Paragraph-aware deterministic chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from app.domain.identity import chunk_identity, content_hash
from app.retrieval import CHUNK_MIN_CHARS, CHUNK_OVERLAP_CHARS, CHUNK_TARGET_CHARS

_PARAGRAPH = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    start_offset: int
    end_offset: int
    content: str
    content_hash: str
    chunk_id: str


def _windows(text: str, target: int, overlap: int) -> List[str]:
    if len(text) <= target:
        return [text] if text else []
    step = max(1, target - overlap)
    out: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target)
        out.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return out


def _pieces(normalized: str, target: int, overlap: int) -> List[str]:
    paragraphs = [part.strip() for part in _PARAGRAPH.split(normalized) if part.strip()]
    raw: List[str] = []
    for para in paragraphs:
        raw.extend(_windows(para, target, overlap) if len(para) > target else [para])
    merged: List[str] = []
    buf = ""
    for piece in raw:
        if not buf:
            buf = piece
            continue
        candidate = f"{buf}\n\n{piece}"
        if len(candidate) <= target:
            buf = candidate
        else:
            merged.append(buf)
            buf = piece
    if buf:
        merged.append(buf)
    return [item for item in merged if len(item) >= CHUNK_MIN_CHARS]


def chunk_document(
    document_id,
    document_content_hash: str,
    normalized_text: str,
    *,
    target: int = CHUNK_TARGET_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> List[ChunkSpec]:
    if not normalized_text.strip():
        return []
    cursor = 0
    specs: List[ChunkSpec] = []
    for index, content in enumerate(_pieces(normalized_text, target, overlap)):
        start = normalized_text.find(content, cursor)
        if start < 0:
            start = normalized_text.find(content)
        end = start + len(content)
        cursor = max(cursor, start + 1)
        digest = content_hash(content)
        specs.append(
            ChunkSpec(
                chunk_index=index,
                start_offset=start,
                end_offset=end,
                content=content,
                content_hash=digest,
                chunk_id=chunk_identity(document_id, document_content_hash, index),
            )
        )
    return specs
