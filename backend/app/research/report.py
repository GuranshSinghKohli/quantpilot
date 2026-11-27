"""Deterministic report assembly from verified claims. The model cannot add facts."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.core.settings import get_settings
from app.domain.enums import ClaimKind, VerificationStatus
from app.research.schemas import ReportItem, ResearchReport, VerifiedClaim
from app.research.security import validate_report_market_bindings
from app.research.verify import used_citations

_BANNED = re.compile(r"\b(buy|sell|hold|price target|expected return|caused|causes)\b", re.I)
_RELATION = re.compile(r"\b(partner|acquir|relationship|develops|uses|invest)\w*\b", re.I)


def _item(claim: VerifiedClaim) -> ReportItem:
    return ReportItem(
        statement=claim.statement,
        finding_type=claim.claim_type,
        claim_id=claim.claim_id,
        citation_ids=list(claim.citation_ids),
        source_ids=list(claim.source_ids),
        evidence_ids=list(claim.evidence_ids),
        entity_id=claim.entity_id,
        agent=claim.agent,
        provenance=claim.provenance,
    )


def _cap(items: Sequence[VerifiedClaim], limit: int) -> List[VerifiedClaim]:
    return list(items)[:limit]


def filter_summary(bullets: Sequence[str], allowed: Set[str]) -> Tuple[List[str], int]:
    kept: List[str] = []
    dropped = 0
    for bullet in bullets:
        text = bullet.strip()
        if text in allowed and text not in kept:
            kept.append(text)
        else:
            dropped += 1
    return kept, dropped


def assemble_report(
    *,
    query: str,
    claims: Sequence[VerifiedClaim],
    package: Dict[str, Any],
    summary_bullets: Optional[Sequence[str]] = None,
    limitations: Optional[Sequence[str]] = None,
) -> Tuple[ResearchReport, int]:
    """Build a report. Returns the report and how many summary bullets were rejected."""
    settings = get_settings()
    cap = settings.RESEARCH_MAX_FINDINGS
    supported = [c for c in claims if c.verification_status is VerificationStatus.SUPPORTED and not _BANNED.search(c.statement)]
    facts = _cap([c for c in supported if c.claim_type is ClaimKind.FACT and c.citation_ids], cap)
    observations = _cap(
        [c for c in supported if c.claim_type is ClaimKind.OBSERVATION and c.agent == "market" and c.citation_ids],
        cap,
    )
    relationships = _cap(
        [c for c in facts if c.agent == "company" and _RELATION.search(c.statement)],
        cap,
    )
    technology = _cap([c for c in facts if c.agent == "technology"], cap)
    implications = _cap(
        [c for c in claims if c.claim_type is ClaimKind.INFERENCE and c.verification_status is VerificationStatus.UNCERTAIN and c.citation_ids],
        cap,
    )
    scenarios = _cap(
        [c for c in claims if c.claim_type is ClaimKind.SCENARIO and c.verification_status is VerificationStatus.UNCERTAIN and c.citation_ids],
        cap,
    )
    conflicts = _cap([c for c in claims if c.verification_status is VerificationStatus.CONFLICTING and c.citation_ids], cap)

    allowed = {c.statement for c in [*facts, *observations]}
    draft = list(summary_bullets) if summary_bullets is not None else [c.statement for c in [*facts, *observations]]
    kept, dropped = filter_summary(draft, allowed)
    summary_claims = []
    by_statement = {c.statement: c for c in [*facts, *observations]}
    for text in kept[: settings.RESEARCH_MAX_SUMMARY_ITEMS]:
        summary_claims.append(by_statement[text])

    notes = list(limitations or [])
    if not facts and not observations:
        notes.append("insufficient evidence for a verified factual section")
    if any(c.verification_status is VerificationStatus.UNSUPPORTED for c in claims):
        notes.append("unsupported claims were excluded from factual sections")
    if conflicts:
        notes.append("conflicting sources are both retained; neither side was selected")
    banned = [c for c in claims if _BANNED.search(c.statement)]
    if banned:
        notes.append("investment language and unsupported causal wording were excluded")

    cited = used_citations([*facts, *observations, *relationships, *technology, *implications, *scenarios, *conflicts], package)
    report = ResearchReport(
        title=(query.strip() or "Research")[:180],
        executive_summary=[_item(c) for c in summary_claims],
        key_facts=[_item(c) for c in facts] or None,
        market_observations=[_item(c) for c in observations] or None,
        company_relationships=[_item(c) for c in relationships] or None,
        technology_findings=[_item(c) for c in technology] or None,
        implications=[_item(c) for c in implications] or None,
        scenarios=[_item(c) for c in scenarios] or None,
        conflicts=[_item(c) for c in conflicts] or None,
        limitations=list(dict.fromkeys(notes))[:12],
        citations=cited[: settings.RESEARCH_MAX_EVIDENCE],
        metadata={
            "plan_version": "plan-v1",
            "summary_rejected": dropped,
            "factual_items": len(facts) + len(observations),
            **(
                {"resolved_securities": list(package.get("resolved_securities") or [])}
                if "resolved_securities" in package
                else {}
            ),
        },
    )
    return validate_report_market_bindings(report), dropped


def factual_items(report: ResearchReport) -> Iterable[ReportItem]:
    for section in (
        report.executive_summary,
        report.key_facts or [],
        report.market_observations or [],
        report.company_relationships or [],
        report.technology_findings or [],
    ):
        yield from section
