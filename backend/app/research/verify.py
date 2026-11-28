"""Deterministic hallucination guard. Confidence never overrides evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from app.domain.enums import ClaimKind, VerificationStatus
from app.research.schemas import AgentFinding, VerifiedClaim

_NEG = re.compile(r"\b(did not|does not|do not|didn't|doesn't|not|no longer|denied|never)\b", re.I)
_REL_VERB = re.compile(
    r"\b(partner|partnership|acquir|acquisition|invest|suppl|compet|uses|develop|owns|depend)\w*\b",
    re.I,
)
_INFERENCE_CUE = re.compile(r"\b(may|might|could|suggests|appears|likely|possibly)\b", re.I)
_ADVICE = re.compile(
    r"\b(buy|sell|hold|price target|expected return|portfolio allocation|trading strategy)\b",
    re.I,
)
_CAUSAL = re.compile(r"\b(caused|cause[sd]?|because of|due to the (event|announcement))\b", re.I)
_TOKEN = re.compile(r"[a-z0-9]+")
_REL_ALIASES = {
    "PARTNERED_WITH": ("partner", "partnership"),
    "ACQUIRED": ("acquir", "acquisition"),
    "INVESTED_IN": ("invest",),
    "SUPPLIES": ("suppl",),
    "COMPETES_WITH": ("compet",),
    "USES": ("uses", "use"),
    "DEVELOPS": ("develop",),
    "OWNS": ("owns", "own"),
    "DEPENDS_ON": ("depend",),
    "CUSTOMERS_OF": ("customer",),
    "CUSTOMER_OF": ("customer",),
}


def verify_findings(
    findings: Sequence[AgentFinding],
    package: Dict[str, Any],
) -> List[VerifiedClaim]:
    citations = {item["citation_id"]: item for item in package.get("citations") or [] if item.get("citation_id")}
    claims: List[VerifiedClaim] = []
    for finding in findings:
        claims.append(_verify_one(finding, package, citations))
    return _mark_conflicts(claims)


def _verify_one(
    finding: AgentFinding,
    package: Dict[str, Any],
    citations: Dict[str, Dict[str, Any]],
) -> VerifiedClaim:
    issues: List[str] = []
    kind = finding.finding_type
    if _ADVICE.search(finding.statement):
        issues.append("investment_language")
    if finding.agent == "market" and _CAUSAL.search(finding.statement):
        issues.append("causal_claim")

    cited = list(dict.fromkeys([*finding.citations, *finding.evidence_refs]))
    resolved = []
    for cid in cited:
        row = citations.get(cid)
        if row is None:
            issues.append("unknown_citation")
        else:
            resolved.append(row)
    valid_ids = [row["citation_id"] for row in resolved]

    if kind in {ClaimKind.FACT, ClaimKind.OBSERVATION} and not valid_ids:
        issues.append("missing_citation")
    if kind in {ClaimKind.INFERENCE, ClaimKind.SCENARIO} and not valid_ids:
        issues.append("missing_citation")
    if kind is ClaimKind.FACT and _INFERENCE_CUE.search(finding.statement):
        issues.append("classification_inference_as_fact")
        kind = ClaimKind.INFERENCE
    if _is_relationship_fact(finding, kind) and not _relationship_supported(finding, package, resolved):
        issues.append("unsupported_relationship")
    if _structured_fact_ungrounded(finding.statement, package, resolved):
        issues.append("ungrounded_structured_fact")

    status = _status(kind, issues, valid_ids)
    return VerifiedClaim(
        claim_id=_claim_id(finding.finding_id, status.value),
        finding_id=finding.finding_id,
        statement=finding.statement,
        claim_type=kind,
        verification_status=status,
        confidence=finding.confidence,
        citation_ids=valid_ids,
        evidence_refs=list(finding.evidence_refs),
        agent=finding.agent,
        issues=list(dict.fromkeys(issues)),
    )


def _status(kind: ClaimKind, issues: Sequence[str], valid_ids: Sequence[str]) -> VerificationStatus:
    blocking = {
        "missing_citation",
        "unknown_citation",
        "unsupported_relationship",
        "ungrounded_structured_fact",
        "investment_language",
        "causal_claim",
    }
    if any(item in blocking for item in issues):
        return VerificationStatus.UNSUPPORTED
    if kind in {ClaimKind.INFERENCE, ClaimKind.SCENARIO}:
        return VerificationStatus.UNCERTAIN if valid_ids else VerificationStatus.UNSUPPORTED
    if "classification_inference_as_fact" in issues:
        return VerificationStatus.UNCERTAIN
    return VerificationStatus.SUPPORTED


# Words that describe a listing. They are not evidence. A token outside this
# set and outside the cited entity's recorded attributes is not supported.
_LISTING_WORDS = frozenset({
    "the", "a", "an", "of", "for", "on", "in", "is", "are", "was", "were",
    "be", "been", "and", "or", "with", "this", "its", "their", "company",
    "legal", "name", "ticker", "symbol", "cik", "exchange", "listed",
    "listing", "recorded", "sec", "identity", "canonical",
})


def _structured_fact_ungrounded(
    statement: str,
    package: Dict[str, Any],
    resolved: Sequence[Dict[str, Any]],
) -> bool:
    """A structured listing citation does not support words it does not record."""
    if not resolved or any(row.get("kind") != "structured_fact" for row in resolved):
        return False
    titles = {str(row.get("title") or "").strip().lower() for row in resolved if row.get("title")}
    allowed = set(_LISTING_WORDS)
    for entity in package.get("entities") or []:
        name = str(entity.get("canonical_name") or entity.get("legal_name") or "").strip().lower()
        if titles and name not in titles:
            continue
        for value in (
            entity.get("canonical_name"),
            entity.get("legal_name"),
            entity.get("ticker"),
            entity.get("cik"),
            entity.get("exchange"),
            entity.get("entity_type"),
        ):
            allowed.update(re.findall(r"[a-z0-9]+", str(value or "").lower()))
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", statement.lower()) if len(tok) > 1]
    return any(tok not in allowed for tok in tokens)


def _is_relationship_fact(finding: AgentFinding, kind: ClaimKind) -> bool:
    if kind is not ClaimKind.FACT:
        return False
    return bool(_REL_VERB.search(finding.statement))


def _relationship_supported(
    finding: AgentFinding,
    package: Dict[str, Any],
    resolved: Sequence[Dict[str, Any]],
) -> bool:
    if any(row.get("kind") == "graph_fact" and _graph_matches(finding.statement, package, row) for row in resolved):
        return True
    if any(row.get("kind") == "event_fact" for row in resolved):
        event_ids = {str(row.get("locator", {}).get("event_id") or "") for row in resolved}
        for event in package.get("events") or []:
            if str(event.get("event_id")) in event_ids and _REL_VERB.search(str(event.get("title") or "")):
                return True
            if str(event.get("event_id")) in event_ids and str(event.get("event_type") or "") in {
                "partnership",
                "acquisition",
                "investment",
            }:
                return True
    for row in resolved:
        if row.get("kind") != "document_excerpt":
            continue
        excerpt = _excerpt_for(package, row)
        if excerpt and _REL_VERB.search(excerpt) and _names_overlap(finding.statement, excerpt):
            return True
    return False


def _graph_matches(statement: str, package: Dict[str, Any], citation: Dict[str, Any]) -> bool:
    evidence_id = str(citation.get("evidence_id") or "")
    title = str(citation.get("title") or "")
    for edge in package.get("graph_facts") or []:
        if evidence_id and str(edge.get("evidence_id") or "") != evidence_id:
            continue
        rel = str(edge.get("relationship") or title)
        aliases = _REL_ALIASES.get(rel, (rel.lower(),))
        lowered = statement.lower()
        if any(alias in lowered for alias in aliases):
            return True
        if rel.lower() in lowered:
            return True
    return False


def _excerpt_for(package: Dict[str, Any], citation: Dict[str, Any]) -> str:
    doc_id = str(citation.get("document_id") or "")
    chunk_id = str(citation.get("chunk_id") or "")
    for doc in package.get("documents") or []:
        if str(doc.get("document_id") or "") == doc_id or str(doc.get("chunk_id") or "") == chunk_id:
            return f"{doc.get('title') or ''} {doc.get('excerpt') or ''}"
    return str(citation.get("title") or "")


def _names_overlap(statement: str, excerpt: str) -> bool:
    left = set(_TOKEN.findall(statement.lower()))
    right = set(_TOKEN.findall(excerpt.lower()))
    return len(left & right) >= 2


def _mark_conflicts(claims: List[VerifiedClaim]) -> List[VerifiedClaim]:
    pairs: List[Tuple[int, int]] = []
    for i, left in enumerate(claims):
        for j, right in enumerate(claims):
            if j <= i:
                continue
            if _contradicts(left.statement, right.statement):
                pairs.append((i, j))
    if not pairs:
        return claims
    out = [claim.model_copy(deep=True) for claim in claims]
    for i, j in pairs:
        out[i].verification_status = VerificationStatus.CONFLICTING
        out[j].verification_status = VerificationStatus.CONFLICTING
        if "conflict" not in out[i].issues:
            out[i].issues.append("conflict")
        if "conflict" not in out[j].issues:
            out[j].issues.append("conflict")
    return out


def _contradicts(left: str, right: str) -> bool:
    a_neg = bool(_NEG.search(left))
    b_neg = bool(_NEG.search(right))
    if a_neg == b_neg:
        return False
    a_tokens = _content_tokens(left)
    b_tokens = _content_tokens(right)
    if not a_tokens or not b_tokens:
        return False
    if not (_REL_VERB.search(left) and _REL_VERB.search(right)):
        return False
    overlap = len(a_tokens & b_tokens)
    return overlap >= 2


def _content_tokens(text: str) -> set[str]:
    cleaned = _NEG.sub(" ", text.lower())
    stop = {"the", "and", "with", "from", "announced", "source", "reports", "a", "an"}
    return {tok for tok in _TOKEN.findall(cleaned) if tok not in stop}


def _claim_id(finding_id: str, status: str) -> str:
    return hashlib.sha256(f"{finding_id}|{status}".encode("utf-8")).hexdigest()[:16]


def citation_index(package: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {item["citation_id"]: item for item in package.get("citations") or [] if item.get("citation_id")}


def used_citations(claims: Iterable[VerifiedClaim], package: Dict[str, Any]) -> List[Dict[str, Any]]:
    index = citation_index(package)
    seen = []
    for claim in claims:
        for cid in claim.citation_ids:
            row = index.get(cid)
            if row and row not in seen:
                seen.append(row)
    return seen
