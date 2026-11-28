"""Cutoff enforcement. Future sources never reach production scoring inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional

from evals.schemas import EvidenceFixture


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        try:
            stamp = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def filter_evidence(
    items: Iterable[EvidenceFixture],
    cutoff: datetime,
    *,
    allow_retrospective: bool = False,
) -> List[EvidenceFixture]:
    if allow_retrospective:
        return list(items)
    kept: List[EvidenceFixture] = []
    for item in items:
        published = parse_time(item.published_at)
        if published is None:
            # Undated non-price sources are withheld; price/macro may proceed.
            if item.source_type in {"price", "macro", "options"}:
                kept.append(item)
            continue
        if published <= cutoff:
            kept.append(item)
    return kept


def is_future(published_at: Optional[str], cutoff: datetime) -> bool:
    stamp = parse_time(published_at)
    if stamp is None:
        return False
    return stamp > cutoff
