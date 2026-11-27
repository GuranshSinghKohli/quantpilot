"""Entity and symbol checks for event-impact rows.

A market window is usable only when its stored entity_id and symbol are the
same pair as a resolved security. A mismatch is rejected. Nothing is rewritten
onto another ticker.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

WINDOW_DEFINITION = "-1D,+1D,+3D,+5D,+20D"

UNVERIFIED_LIMITATION = "Market impact unavailable: security identity could not be verified."
REJECTED_LIMITATION = "Market impact rejected: impact security did not match the resolved security."


def impact_symbol(impact: Mapping[str, Any]) -> str:
    raw = impact.get("symbol")
    if not raw:
        asset = impact.get("asset") or {}
        if isinstance(asset, Mapping):
            raw = asset.get("symbol")
    return str(raw or "").strip().upper()


def impact_entity_id(impact: Mapping[str, Any]) -> str:
    return str(impact.get("entity_id") or "").strip()


def market_binding_status(impact: Mapping[str, Any], securities: Sequence[Mapping[str, Any]]) -> str:
    """Return accepted, symbol_mismatch, entity_mismatch, or unbound.

    accepted requires one resolved security whose entity_id and symbol both
    equal the impact. Matching only one of the two is a hard rejection.
    """
    entity_id = impact_entity_id(impact)
    symbol = impact_symbol(impact)
    if not securities or not entity_id or not symbol:
        return "unbound"
    entity_hits = [row for row in securities if str(row.get("entity_id") or "") == entity_id]
    symbol_hits = [row for row in securities if str(row.get("symbol") or "").strip().upper() == symbol]
    both = [
        row
        for row in securities
        if str(row.get("entity_id") or "") == entity_id
        and str(row.get("symbol") or "").strip().upper() == symbol
    ]
    if len(both) == 1:
        return "accepted"
    if entity_hits and not both:
        return "symbol_mismatch"
    if symbol_hits and not both:
        return "entity_mismatch"
    return "unbound"


def invariant_issues(
    impact: Mapping[str, Any],
    citation: Mapping[str, Any],
    securities: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Reject a market citation that is not the same security and event as the impact.

    A mismatch is not rewritten onto the other symbol.
    """
    issues: List[str] = []
    impact_entity = impact_entity_id(impact)
    cite_entity = impact_entity_id(citation)
    impact_sym = impact_symbol(impact)
    cite_sym = impact_symbol(citation)
    if not impact_entity or not cite_entity or impact_entity != cite_entity:
        issues.append("entity_mismatch")
    if not impact_sym or not cite_sym or impact_sym != cite_sym:
        issues.append("symbol_mismatch")
    impact_event = str(impact.get("event_id") or "").strip()
    cite_event = str(citation.get("event_id") or "").strip()
    if impact_event and cite_event and impact_event != cite_event:
        issues.append("event_mismatch")
    elif bool(impact_event) != bool(cite_event):
        issues.append("event_mismatch")
    if market_binding_status(impact, securities) != "accepted":
        issues.append("security_mismatch")
    if market_binding_status(citation, securities) != "accepted":
        issues.append("security_mismatch")
    return list(dict.fromkeys(issues))


def matching_security(
    impact: Mapping[str, Any], securities: Sequence[Mapping[str, Any]]
) -> Optional[Mapping[str, Any]]:
    if market_binding_status(impact, securities) != "accepted":
        return None
    entity_id = impact_entity_id(impact)
    symbol = impact_symbol(impact)
    for row in securities:
        if str(row.get("entity_id") or "") == entity_id and str(row.get("symbol") or "").strip().upper() == symbol:
            return row
    return None
