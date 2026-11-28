"""Phase 3 — decide whether collected evidence is enough to verify."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from app.agents.investigation_planner_agent import BROWSER_TOOLS, VALID_TOOLS
from app.models.investigation_plan_schemas import EvidenceAssessment, ResearchTask
from app.services.tool_router import has_official_presentation, presentation_needed


def _api_evidence_thin(items: Sequence[Dict[str, Any]]) -> bool:
    news = [i for i in items if i.get("source_type") == "news"]
    filings = [i for i in items if i.get("source_type") == "filing"]
    ir = [i for i in items if i.get("source_type") == "ir_page"]
    if ir:
        return False
    substantive_news = [
        n
        for n in news
        if n.get("title") and "unavailable" not in (n.get("title") or "").lower()
    ]
    return len(substantive_news) < 2 and len(filings) < 1


def _is_failure_item(item: Dict[str, Any]) -> bool:
    title = (item.get("title") or "").lower()
    excerpt = (item.get("excerpt") or "").lower()
    payload = item.get("raw_payload") or {}
    if payload.get("error"):
        return True
    return any(token in title or token in excerpt for token in ("unavailable", "failed", "disabled"))


def assess_evidence(
    evidence: Sequence[Dict[str, Any]],
    *,
    tasks: Optional[Iterable[ResearchTask]] = None,
    hypotheses: Optional[Sequence[str]] = None,
    asset_hint: str = "equity",
    used_tools: Optional[Iterable[str]] = None,
    browser_enabled: bool = True,
    hypothesis_records: Optional[Sequence[Any]] = None,
) -> EvidenceAssessment:
    """Deterministic sufficiency check. Does not invent missing facts."""
    items = list(evidence or [])
    counts: Dict[str, int] = {}
    for item in items:
        key = str(item.get("source_type") or "other")
        counts[key] = counts.get(key, 0) + 1

    news = [i for i in items if i.get("source_type") == "news" and not _is_failure_item(i)]
    filings = [i for i in items if i.get("source_type") == "filing" and not _is_failure_item(i)]
    ir = [i for i in items if i.get("source_type") == "ir_page" and not _is_failure_item(i)]
    price = [i for i in items if i.get("source_type") == "price"]
    failed_items = [i for i in items if _is_failure_item(i)]

    task_list = list(tasks or [])
    failed_tools = [
        t.required_tool
        for t in task_list
        if t.status == "failed" and t.required_tool
    ]
    for item in failed_items:
        tool = (item.get("raw_payload") or {}).get("tool")
        if tool and tool not in failed_tools:
            failed_tools.append(str(tool))

    used: Set[str] = set(used_tools or [])
    used.update(t.required_tool for t in task_list if t.status == "completed")

    missing: List[str] = []
    next_tools: List[str] = []
    thin = _api_evidence_thin(items)

    if len(news) < 2:
        missing.append("substantive company/industry news")
        if "get_stock_news" not in used:
            next_tools.append("get_stock_news")
    if asset_hint != "etf" and len(filings) < 1:
        missing.append("recent SEC filings")
        if "get_recent_filings" not in used:
            next_tools.append("get_recent_filings")
    if thin and browser_enabled and asset_hint != "etf":
        missing.append("official investor-relations primary source")
        for tool in ("get_ir_materials", "get_shareholder_letter"):
            if tool not in used and tool in VALID_TOOLS:
                next_tools.append(tool)
    needs_deck = presentation_needed(list(hypothesis_records or []) or list(hypotheses or []))
    if (
        needs_deck
        and browser_enabled
        and asset_hint != "etf"
        and not has_official_presentation(items)
    ):
        if "get_ir_materials" not in used:
            if "get_ir_materials" not in next_tools:
                next_tools.append("get_ir_materials")
            if "latest official investor presentation" not in missing:
                missing.append("latest official investor presentation")
        elif "browser_find_document" not in used:
            missing.append("latest official investor presentation")
            next_tools.append("browser_find_document")
    if not price:
        missing.append("price/move context")
        if "get_stock_price" not in used:
            next_tools.append("get_stock_price")
    if failed_tools:
        missing.append("successful retrieval for failed tools")

    # Cap recommended tools to unused, allowed names.
    recommended: List[str] = []
    seen: Set[str] = set()
    for tool in next_tools:
        if tool in seen or tool not in VALID_TOOLS:
            continue
        if tool in BROWSER_TOOLS and not browser_enabled:
            continue
        seen.add(tool)
        recommended.append(tool)

    non_price = [i for i in items if i.get("source_type") != "price" and not _is_failure_item(i)]
    sufficient = (not thin and len(non_price) >= 1) or (
        len(news) >= 2 or len(filings) >= 1 or len(ir) >= 1
    )
    if not items:
        sufficient = False

    notes = []
    if sufficient:
        notes.append("Evidence coverage meets the investigation bar.")
    else:
        notes.append("Evidence is thin or incomplete; another research pass may help.")
    if failed_tools:
        notes.append(f"Failed tools: {', '.join(failed_tools)}.")

    hyp_names = [h for h in (hypotheses or []) if h]
    supported: List[str] = []
    weakened: List[str] = []
    if hyp_names:
        if sufficient:
            supported = hyp_names[:2]
            weakened = hyp_names[2:]
        else:
            weakened = list(hyp_names)

    contradictions = _detect_contradictions(items)

    return EvidenceAssessment(
        sufficient_evidence=bool(sufficient),
        supported_hypotheses=supported,
        weakened_hypotheses=weakened,
        contradictions=contradictions,
        missing_evidence=missing,
        recommended_next_tools=recommended,
        failed_tools=failed_tools,
        notes=notes,
        evidence_counts=counts,
    )


def _detect_contradictions(items: Sequence[Dict[str, Any]]) -> List[str]:
    """Surface obvious title-level conflicts. Does not invent facts."""
    texts = [
        f"{item.get('title') or ''} {item.get('excerpt') or ''}".lower()
        for item in items
        if not _is_failure_item(item)
    ]
    if not texts:
        return []
    pairs = (
        ("beat", "miss"),
        ("upgrade", "downgrade"),
        ("guidance raised", "guidance cut"),
        ("raised outlook", "cut outlook"),
    )
    found: List[str] = []
    blob = " ".join(texts)
    for left, right in pairs:
        if left in blob and right in blob:
            found.append(f"Evidence mentions both '{left}' and '{right}'.")
    return found
