"""Market observations are rendered from the impact row, then checked.

An LLM finding that contains a return is dropped. The replacement statement
uses one window of one security.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, List, Mapping, Optional, Sequence

from app.domain.enums import ClaimKind
from app.market.binding import market_binding_status
from app.market.render import WINDOW_ORDER, iter_market_observations, render_market_statement
from app.research.schemas import AgentFinding

_MARKET_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*%|\b\d+\.\d+\b")


def ground_market_findings(
    findings: Sequence[AgentFinding],
    package: Mapping[str, Any],
) -> List[AgentFinding]:
    rendered = render_findings(package)
    kept: List[AgentFinding] = []
    for item in findings:
        if item.finding_type in {ClaimKind.FACT, ClaimKind.OBSERVATION}:
            continue
        if _MARKET_NUMBER.search(item.statement):
            continue
        kept.append(item)
    return (rendered + kept)[:12]


def render_findings(package: Mapping[str, Any]) -> List[AgentFinding]:
    findings: List[AgentFinding] = []
    for fact in package.get("market_facts") or []:
        citation_id = _citation_id(package, fact)
        if not citation_id:
            continue
        for row in iter_market_observations(fact):
            findings.append(
                AgentFinding(
                    finding_id=_finding_id(row["statement"], citation_id),
                    agent="market",
                    statement=row["statement"],
                    finding_type=ClaimKind.OBSERVATION,
                    evidence_refs=[citation_id],
                    citations=[citation_id],
                    confidence=1.0,
                    reasoning_summary="rendered from the stored impact window",
                    provenance=row,
                )
            )
    return findings


def market_observation_issues(
    statement: str,
    resolved: Sequence[Mapping[str, Any]],
    package: Mapping[str, Any],
) -> List[str]:
    market_rows = [row for row in resolved if row.get("kind") == "market_fact"]
    if not market_rows:
        return ["market_identity_mismatch"]
    securities = list(package.get("resolved_securities") or [])
    issues: List[str] = []
    matched = False
    for row in market_rows:
        market = row.get("market") or {}
        if not market.get("entity_id") or not market.get("symbol") or not market.get("event_id"):
            issues.append("market_identity_mismatch")
            continue
        if securities and market_binding_status(market, securities) != "accepted":
            issues.append("security_mismatch")
            continue
        for window in WINDOW_ORDER:
            rendered = render_market_statement(market, window)
            if rendered and rendered == statement:
                matched = True
    if not matched:
        issues.append("market_number_mismatch")
    return list(dict.fromkeys(issues))


def _finding_id(statement: str, citation_id: str) -> str:
    key = f"market|{statement}|{citation_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _citation_id(package: Mapping[str, Any], fact: Mapping[str, Any]) -> Optional[str]:
    event_id = str(fact.get("event_id") or (fact.get("source") or {}).get("event_id") or "")
    symbol = str(fact.get("symbol") or "").strip().upper()
    for item in package.get("citations") or []:
        if item.get("kind") != "market_fact":
            continue
        market = item.get("market") or {}
        if str(market.get("event_id") or "") == event_id and str(market.get("symbol") or "").upper() == symbol:
            return str(item.get("citation_id") or "") or None
    return None
