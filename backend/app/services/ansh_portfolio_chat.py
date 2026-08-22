"""ANSH — OpenClaw-branded portfolio advisor chat over the user's basket."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_text
from app.models.agent_schemas import PortfolioAnalysis
from app.observability.logger import get_logger, log_event

logger = get_logger("ansh_portfolio_chat")

SYSTEM_PROMPT = """You are ANSH, QuantPilot's OpenClaw portfolio advisor.

Personality: direct, concise, slightly sharp — like a sharp research associate.
You are NOT a licensed advisor. Always end material opinions with a one-line disclaimer
that this is educational analysis, not financial advice.

Your job:
1. Understand what the investor wants (growth, income, preservation, etc.).
2. Respect their time horizon (short-term vs long-term) and risk tolerance.
3. Opine on THEIR current portfolio basket using the provided holdings snapshot.
4. Call out concentration, risk mix, weakest links, and fit vs their stated prefs.
5. Suggest 2–4 concrete next checks (not stock tips as orders) — e.g. "investigate X move",
   "trim if HIGH risk clashes with conservative goal".

Keep answers under ~250 words unless they ask for more. Use short paragraphs or bullets.
If the basket is empty, tell them to add holdings first."""


def _analysis_dossier(analysis: PortfolioAnalysis) -> str:
    lines = [
        f"Holdings count: {len(analysis.holdings)}",
        f"Summary: {analysis.summary or 'n/a'}",
        f"Risk mix: {analysis.risk_mix}",
        f"Avg P/E: {analysis.avg_pe}",
        f"Weakest ticker: {analysis.weakest_ticker or 'n/a'}",
        f"Weighted by real positions: {bool(analysis.weighted_by_real_positions)}",
        f"Total market value: {analysis.total_market_value}",
        "Holdings:",
    ]
    for h in analysis.holdings[:20]:
        lines.append(
            f"- {h.ticker}: risk={h.risk_level}, valuation={h.valuation}, "
            f"weight={h.weight_pct:.1f}%, pe={h.pe_ratio}, price={h.current_price}, "
            f"shares={h.shares}, avg_cost={h.avg_cost}, "
            f"unrealized%={h.unrealized_gain_pct}"
        )
    return "\n".join(lines)


def _fallback(
    analysis: PortfolioAnalysis,
    *,
    horizon: Optional[str],
    risk_tolerance: Optional[str],
    goal: Optional[str],
) -> str:
    n = len(analysis.holdings)
    if n == 0:
        return (
            "I'm ANSH. Your portfolio basket is empty — add tickers (Watchlist / "
            "+ Portfolio) first, then come back and I'll size up the mix against "
            "your horizon and risk prefs.\n\n"
            "Educational only — not financial advice."
        )

    horizon_l = (horizon or "unspecified").replace("_", " ")
    risk_l = (risk_tolerance or "unspecified").replace("_", " ")
    goal_l = (goal or "general portfolio review").strip()
    high = analysis.risk_mix.get("HIGH", 0)
    weak = analysis.weakest_ticker or "n/a"

    fit_notes: List[str] = []
    if risk_tolerance == "conservative" and high:
        fit_notes.append(
            f"You asked for conservative risk, but {high} holding(s) screen HIGH — "
            "that may be a mismatch."
        )
    if risk_tolerance == "aggressive" and high == 0:
        fit_notes.append(
            "You want aggressive risk, yet nothing screens HIGH — the book may be "
            "more defensive than the goal."
        )
    if horizon == "short" and high:
        fit_notes.append(
            "Short horizon + HIGH-risk names raises path-dependency risk — "
            "worth investigating those moves first."
        )
    if not fit_notes:
        fit_notes.append(
            "Heuristic screen looks broadly consistent with what you shared; "
            "still dig into the weakest link before sizing up."
        )

    tickers = ", ".join(h.ticker for h in analysis.holdings[:8])
    return (
        f"I'm ANSH. Here's a quick take on your {n}-name basket "
        f"({tickers}{'…' if n > 8 else ''}).\n\n"
        f"**Your prefs:** {goal_l} · horizon: {horizon_l} · risk: {risk_l}\n\n"
        f"**Screen:** {analysis.summary or 'Basket loaded.'} "
        f"Weakest link: {weak}.\n\n"
        f"**Fit vs prefs:** {fit_notes[0]}\n\n"
        "**Next checks:**\n"
        f"1. Investigate why {weak} is the weak link.\n"
        "2. Re-check concentration if a few names dominate weight.\n"
        "3. Align HIGH-risk names with your stated risk tolerance.\n\n"
        "Educational analysis only — not financial advice. "
        "(Full LLM opinions need OPENAI_API_KEY on the API.)"
    )


async def ansh_reply(
    analysis: PortfolioAnalysis,
    *,
    message: str,
    horizon: Optional[str] = None,
    risk_tolerance: Optional[str] = None,
    goal: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    dossier = _analysis_dossier(analysis)
    history_lines: List[str] = []
    for turn in (history or [])[-8:]:
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if content:
            history_lines.append(f"{role.upper()}: {content}")

    prefs = (
        f"Investor goal: {goal or 'not specified'}\n"
        f"Time horizon: {horizon or 'not specified'}\n"
        f"Risk tolerance: {risk_tolerance or 'not specified'}\n"
    )

    user_prompt = (
        f"{prefs}\n"
        f"PORTFOLIO SNAPSHOT:\n{dossier}\n\n"
    )
    if history_lines:
        user_prompt += "RECENT CHAT:\n" + "\n".join(history_lines) + "\n\n"
    user_prompt += f"USER MESSAGE:\n{message.strip()}\n\nReply as ANSH."

    fallback = _fallback(
        analysis,
        horizon=horizon,
        risk_tolerance=risk_tolerance,
        goal=goal,
    )

    answer = await call_openai_text(
        SYSTEM_PROMPT,
        user_prompt,
        fallback=fallback,
    )

    log_event(
        logger,
        logging.INFO,
        "ANSH portfolio reply",
        holdings=len(analysis.holdings),
        horizon=horizon or "",
        risk_tolerance=risk_tolerance or "",
        used_fallback=answer == fallback,
    )

    sources = ["portfolio_analyze", "ansh"]
    if analysis.weakest_ticker:
        sources.append(analysis.weakest_ticker)
    for h in analysis.holdings[:5]:
        if h.ticker not in sources:
            sources.append(h.ticker)

    return {
        "ticker": "PORTFOLIO",
        "answer": answer,
        "sources_used": sources,
    }
