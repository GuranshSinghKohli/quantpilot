"""Portfolio Sync Agent (Phase 11 extension) — extracts real positions from a
pasted or OpenClaw-snapshotted brokerage page, broker-agnostic.

No credentials ever pass through this agent: input is plain page text the
user pasted, or a snapshot from *their own* already-authenticated browser
session via OpenClaw. We never see the login flow.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_json
from app.models.portfolio_sync_schemas import PortfolioSyncOutput, SyncedPosition
from app.observability.logger import get_logger, log_event

logger = get_logger("portfolio_sync_agent")

_TICKER_ROW_RE = re.compile(
    r"\b([A-Z]{1,5})\b[^\n$0-9%-]{0,40}?"
    r"([\d,]+(?:\.\d+)?)\s*(?:shares?|shrs?|sh\b)?[^\n$0-9%-]{0,25}?"
    r"\$?([\d,]+\.\d{2})",
)
_NOISE_TICKERS = {
    "USD", "ETF", "CASH", "N/A", "NAV", "QTY", "AVG", "COST", "TOTAL",
    "GAIN", "LOSS", "DAY", "YTD", "ACCOUNT", "SYMBOL", "PRICE", "VALUE",
}


def _regex_fallback(raw_text: str) -> List[SyncedPosition]:
    """Best-effort broker-agnostic row extraction when no LLM key is set."""
    positions: List[SyncedPosition] = []
    seen = set()
    for match in _TICKER_ROW_RE.finditer(raw_text or ""):
        ticker = match.group(1).upper()
        if ticker in _NOISE_TICKERS or ticker in seen or len(ticker) < 1:
            continue
        try:
            shares = float(match.group(2).replace(",", ""))
            price_like = float(match.group(3).replace(",", ""))
        except ValueError:
            continue
        if shares <= 0 or shares > 1_000_000:
            continue
        seen.add(ticker)
        positions.append(
            SyncedPosition(
                ticker=ticker,
                shares=round(shares, 4),
                avg_cost=None,
                market_value=round(shares * price_like, 2) if price_like else None,
                raw_line=match.group(0)[:200],
            )
        )
        if len(positions) >= 50:
            break
    return positions


def _fallback(raw_text: str, source: str) -> PortfolioSyncOutput:
    positions = _regex_fallback(raw_text)
    warnings: List[str] = []
    if not positions:
        warnings.append(
            "Could not confidently extract positions with the pattern-matching "
            "fallback. Configure OPENAI_API_KEY for LLM-based extraction, or "
            "paste a cleaner copy of the positions table."
        )
    else:
        warnings.append(
            "Extracted with a pattern-matching fallback (no OpenAI key). "
            "Please review shares/prices before saving."
        )
    return PortfolioSyncOutput(
        positions=positions,
        broker_guess="unknown",
        warnings=warnings,
        confidence_score=0.35 if positions else 0.0,
        source=source,
    )


async def extract_positions(raw_text: str, source: str = "paste") -> PortfolioSyncOutput:
    """Parse arbitrary brokerage positions-page text into structured rows."""
    text = (raw_text or "").strip()
    if not text:
        return PortfolioSyncOutput(
            positions=[],
            broker_guess="unknown",
            warnings=["No page text provided."],
            confidence_score=0.0,
            source=source,
        )

    fallback = _fallback(text, source)
    try:
        result = await call_openai_json(
            system_prompt=(
                "You extract stock positions from raw text copied off a brokerage "
                "'positions' or 'holdings' page (any broker: Fidelity, Schwab, "
                "Robinhood, Vanguard, E*TRADE, etc). The text may include extra "
                "chrome (nav, ads, headers) - ignore it. For each real equity/ETF "
                "position, extract: ticker symbol, number of shares, average cost "
                "per share if shown, and market value if shown. Never invent "
                "numbers that are not present in the text. Skip cash, money-market "
                "sweep, and totals rows. Respond ONLY with JSON: "
                '{"positions": [{"ticker": string, "shares": number|null, '
                '"avg_cost": number|null, "market_value": number|null, '
                '"raw_line": string}], "broker_guess": string, '
                '"warnings": [string,...], "confidence_score": number 0-1}'
            ),
            user_prompt=f"Extract positions from this pasted page text:\n\n{text[:8000]}",
            fallback=fallback.model_dump(),
        )
        parsed = PortfolioSyncOutput.model_validate({**result, "source": source})
        if not parsed.positions:
            return fallback
        return parsed
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Portfolio sync extraction failed",
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback.model_dump()
        fb["warnings"] = fb["warnings"] + [f"LLM extraction error: {exc}"]
        return PortfolioSyncOutput.model_validate(fb)
