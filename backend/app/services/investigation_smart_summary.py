"""Smart Summarize — concise wrap-up for an Evidence Ledger case."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_text
from app.models.investigation_schemas import InvestigationDetail, SmartSummaryResponse


def _human_window(window_label: str) -> str:
    mapping = {
        "1d": "1 day",
        "intraday": "1 day",
        "1w": "1 week",
        "5d": "1 week",
        "1mo": "1 month",
        "3mo": "3 months",
        "6mo": "6 months",
        "1y": "1 year",
        "ytd": "year to date",
    }
    key = (window_label or "1d").strip().lower()
    return mapping.get(key, window_label or "the selected window")


def _move_line(detail: InvestigationDetail) -> str:
    window = _human_window(detail.window_label or "1d")
    ticker = detail.ticker
    move = detail.move_pct
    if move is None:
        return f"{ticker} move over {window} was not cleanly measured."
    abs_move = abs(float(move))
    if float(move) < -0.05:
        return f"{ticker} fell {abs_move:.1f}% over {window}."
    if float(move) > 0.05:
        return f"{ticker} rose {abs_move:.1f}% over {window}."
    return f"{ticker} was roughly flat ({float(move):+.1f}%) over {window}."


def _sorted_claims(detail: InvestigationDetail) -> List[Any]:
    return sorted(detail.claims or [], key=lambda c: c.rank or 999)


def _fallback_summary(detail: InvestigationDetail) -> SmartSummaryResponse:
    claims = _sorted_claims(detail)
    lead = claims[0].statement if claims else None
    bullets: List[str] = [_move_line(detail)]
    if lead:
        bullets.append(f"Leading cause: {lead}")
    for claim in claims[1:4]:
        weight = int(round((claim.confidence_score or 0) * 100))
        bullets.append(f"Alt reason ({weight}%): {claim.statement}")
    if detail.da_outcome and detail.da_outcome.outcome:
        bullets.append(f"Devil's Advocate: {detail.da_outcome.outcome}.")
        if detail.da_outcome.counterargument:
            bullets.append(detail.da_outcome.counterargument[:280])
    if detail.evidence_count or detail.evidence_items:
        n = detail.evidence_count or len(detail.evidence_items or [])
        bullets.append(f"Grounded in {n} evidence item(s).")

    headline = _move_line(detail)
    if lead:
        headline = f"{headline} Likely cause: {lead}"

    takeaway = (
        (detail.summary or "").strip()
        or (lead and f"Best current explanation: {lead}")
        or "Investigation complete — review claims and evidence above."
    )

    return SmartSummaryResponse(
        headline=headline[:400],
        bullets=bullets[:8],
        takeaway=takeaway[:600],
        source="heuristic",
    )


def _dossier(detail: InvestigationDetail) -> str:
    claims = _sorted_claims(detail)
    lines = [
        f"Ticker: {detail.ticker}",
        f"Window: {_human_window(detail.window_label)}",
        f"Move %: {detail.move_pct}",
        f"Status: {detail.status}",
        f"Stored summary: {detail.summary or ''}",
        "Claims (ranked):",
    ]
    for c in claims[:6]:
        lines.append(
            f"- rank={c.rank} weight={c.confidence_score} stance={c.stance}: {c.statement}"
        )
    if detail.da_outcome:
        lines.append(
            f"Devil's Advocate outcome: {detail.da_outcome.outcome}; "
            f"counter: {detail.da_outcome.counterargument or ''}"
        )
    if detail.verification_notes:
        lines.append(f"Verification notes: {detail.verification_notes}")
    if detail.roster and detail.roster.memo:
        one_liner = detail.roster.memo.get("one_liner")
        if one_liner:
            lines.append(f"Memo one-liner: {one_liner}")
    lines.append("Evidence titles:")
    for ev in (detail.evidence_items or [])[:6]:
        lines.append(f"- {ev.title}: {(ev.excerpt or '')[:160]}")
    return "\n".join(lines)


async def smart_summarize_investigation(
    detail: InvestigationDetail,
) -> SmartSummaryResponse:
    """LLM smart summary with deterministic fallback (no API key / errors)."""
    fallback = _fallback_summary(detail)
    system = (
        "You are QuantPilot's Smart Summarize agent. Write a tight wrap-up of a "
        "stock move investigation for a busy reader. Be factual, not advisory. "
        "Return plain text with exactly these labeled sections:\n"
        "HEADLINE: one sentence with ticker, direction, % move, window, and top cause\n"
        "BULLETS:\n- 3 to 6 short bullet lines\n"
        "TAKEAWAY: one sentence bottom line\n"
        "No markdown fences. No investment advice."
    )
    user = (
        "Summarize this Evidence Ledger case:\n\n"
        f"{_dossier(detail)}\n\n"
        "Prefer the leading claim as the cause. Mention Devil's Advocate if it "
        "weakened or demoted the lead."
    )
    raw = await call_openai_text(system, user, fallback="")
    if not raw.strip():
        return fallback
    parsed = _parse_llm_summary(raw)
    if not parsed:
        return fallback
    return parsed


def _parse_llm_summary(raw: str) -> Optional[SmartSummaryResponse]:
    headline = ""
    takeaway = ""
    bullets: List[str] = []
    mode = ""
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        upper = text.upper()
        if upper.startswith("HEADLINE:"):
            headline = text.split(":", 1)[1].strip()
            mode = "headline"
            continue
        if upper.startswith("BULLETS:"):
            mode = "bullets"
            rest = text.split(":", 1)[1].strip()
            if rest:
                bullets.append(rest.lstrip("-• ").strip())
            continue
        if upper.startswith("TAKEAWAY:"):
            takeaway = text.split(":", 1)[1].strip()
            mode = "takeaway"
            continue
        if mode == "bullets" and (
            text.startswith("-") or text.startswith("•") or text[0:1].isdigit()
        ):
            cleaned = text.lstrip("-• ").strip()
            if cleaned:
                # strip leading "1. "
                if len(cleaned) > 2 and cleaned[0].isdigit() and cleaned[1] in ".)":
                    cleaned = cleaned[2:].strip()
                bullets.append(cleaned)
        elif mode == "takeaway" and not takeaway:
            takeaway = text
        elif mode == "headline" and not headline:
            headline = text

    if not headline and not bullets and not takeaway:
        return None
    return SmartSummaryResponse(
        headline=(headline or (bullets[0] if bullets else "Smart summary"))[:400],
        bullets=bullets[:8] or ([takeaway] if takeaway else ["See case details above."]),
        takeaway=(takeaway or headline or "Review the leading claim and evidence.")[:600],
        source="llm",
    )


async def smart_summarize_text(
    *,
    title: str,
    body: str,
) -> SmartSummaryResponse:
    """Generic smart summary for full research reports."""
    clipped = (body or "")[:8000]
    fallback = SmartSummaryResponse(
        headline=title or "Research summary",
        bullets=[
            line.strip()
            for line in clipped.splitlines()
            if line.strip() and len(line.strip()) > 40
        ][:5]
        or ["Report available above — open sections for detail."],
        takeaway="Skim the executive summary and recommendation in the full report.",
        source="heuristic",
    )
    system = (
        "You are QuantPilot's Smart Summarize agent. Compress a research report "
        "into a quick wrap-up. No investment advice. Return plain text with:\n"
        "HEADLINE: one sentence\n"
        "BULLETS:\n- 3 to 6 short bullets\n"
        "TAKEAWAY: one sentence"
    )
    user = f"Title: {title}\n\nReport:\n{clipped}"
    raw = await call_openai_text(system, user, fallback="")
    if not raw.strip():
        return fallback
    return _parse_llm_summary(raw) or fallback
