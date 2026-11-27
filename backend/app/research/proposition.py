"""Compare a claim with the cited excerpt.

A shared topic is not support. The company named in the claim has to be in
the excerpt, and a number has to keep the same owner and the same population.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, Optional, Sequence, Set

from app.graph.normalize import legal_name_stem

_ANCHOR = re.compile(r"\$\s*\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?\s*%")
_WORD = re.compile(r"(?<!\w){phrase}(?!\w)", re.IGNORECASE)
_OWNER = re.compile(r"\b[A-Z][A-Za-z0-9&.-]{2,}\b")
_OWNER_SKIP = {"The", "This", "That", "Source", "According", "However", "After"}
_PERIOD = {
    "quarter": "quarter",
    "year": "year",
    "week": "week",
    "month": "month",
}


def document_support_issues(
    statement: str,
    excerpt: str,
    entities: Sequence[Mapping[str, Any]],
) -> List[str]:
    if not statement or not excerpt:
        return []
    issues: List[str] = []
    if _entity_missing(statement, excerpt, entities):
        issues.append("entity_not_in_source")
    issues.extend(_anchor_issues(statement, excerpt))
    return list(dict.fromkeys(issues))


def _entity_missing(statement: str, excerpt: str, entities: Sequence[Mapping[str, Any]]) -> bool:
    excerpt_low = excerpt.casefold()
    for entity in entities:
        for label in _labels(entity):
            if not _contains(statement, label):
                continue
            if _contains(excerpt, label) or label.casefold() in excerpt_low:
                continue
            return True
    return False


def _labels(entity: Mapping[str, Any]) -> List[str]:
    labels: List[str] = []
    for key in ("canonical_name", "legal_name", "name"):
        value = str(entity.get(key) or "").strip()
        if len(value) >= 3:
            labels.append(value)
        stem = legal_name_stem(value)
        if stem and len(stem) >= 3:
            labels.append(stem)
    ticker = str(entity.get("ticker") or "").strip()
    if len(ticker) >= 2:
        labels.append(ticker)
    return list(dict.fromkeys(labels))


def _contains(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return re.search(_WORD.pattern.format(phrase=re.escape(phrase)), text) is not None


def _anchor_issues(statement: str, excerpt: str) -> List[str]:
    issues: List[str] = []
    for match in _ANCHOR.finditer(statement):
        anchor = _normalize_anchor(match.group(0))
        evidence_at = _find_anchor(excerpt, anchor)
        if evidence_at is None:
            continue
        claim_owners = _nearest_owners(statement, match.start())
        evidence_owners = _nearest_owners(excerpt, evidence_at)
        if claim_owners and evidence_owners and claim_owners.isdisjoint(evidence_owners):
            issues.append("scope_changed")
        claim_pop = _population(statement, match.start())
        evidence_pop = _population(excerpt, evidence_at)
        if claim_pop == "many" and evidence_pop == "one":
            issues.append("population_widened")
        claim_period = _period(statement, match.start())
        evidence_period = _period(excerpt, evidence_at)
        if claim_period and evidence_period and claim_period != evidence_period:
            issues.append("period_changed")
        if _certainty_raised(statement, match.start(), excerpt, evidence_at):
            issues.append("certainty_raised")
    return issues


def _normalize_anchor(token: str) -> str:
    return re.sub(r"[\s$,%]", "", token)


def _find_anchor(text: str, anchor: str) -> Optional[int]:
    for match in _ANCHOR.finditer(text):
        if _normalize_anchor(match.group(0)) == anchor:
            return match.start()
    return None


def _nearest_owners(text: str, index: int) -> Set[str]:
    """The capitalized phrase immediately before the number, not every name in the paragraph."""
    window = text[max(0, index - 180) : index]
    matches = [item for item in _OWNER.finditer(window) if item.group() not in _OWNER_SKIP]
    if not matches:
        return set()
    phrase = [matches[-1].group()]
    cursor = len(matches) - 2
    while cursor >= 0:
        gap = window[matches[cursor].end() : matches[cursor + 1].start()]
        if gap.strip():
            break
        phrase.insert(0, matches[cursor].group())
        cursor -= 1
    return {word.casefold() for word in phrase}


def _population(text: str, index: int) -> Optional[str]:
    window = text[max(0, index - 80) : index + 80].casefold()
    if re.search(r"\banalysts\b", window):
        return "many"
    if re.search(r"\b(one|a single|an)\s+analyst\b", window) or re.search(r"\banalyst\b", window):
        return "one"
    return None


def _period(text: str, index: int) -> Optional[str]:
    window = text[max(0, index - 80) : index + 40].casefold()
    found = {name for token, name in _PERIOD.items() if token in window}
    if len(found) == 1:
        return next(iter(found))
    return None


def _certainty_raised(statement: str, claim_at: int, excerpt: str, evidence_at: int) -> bool:
    claim = statement[max(0, claim_at - 80) : claim_at + 24].casefold()
    evidence = excerpt[max(0, evidence_at - 80) : evidence_at + 24].casefold()
    hedged = bool(re.search(r"\b(could|might|may|expects|expected|if accurate)\b", evidence))
    asserted = bool(re.search(r"\b(grew|reached|is|was|has)\b", claim))
    claim_hedged = bool(re.search(r"\b(could|might|may|expects|if)\b", claim))
    return hedged and asserted and not claim_hedged
