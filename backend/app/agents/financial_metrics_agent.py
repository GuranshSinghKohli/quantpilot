import json
import logging
import traceback
from typing import Any, Dict

from app.agents.llm import call_openai_json
from app.mcp_client import MCPClientError, call_tool
from app.models.agent_schemas import FinancialMetricsAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("financial_metrics_agent")


def _merge_mcp_stock_data(
    price_data: Dict[str, Any], fundamentals: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "ticker": fundamentals.get("ticker") or price_data.get("ticker"),
        "current_price": price_data.get("current_price"),
        "previous_close": price_data.get("previous_close"),
        "price_change_percent": price_data.get("price_change_percent"),
        "volume": price_data.get("volume"),
        "market_cap": fundamentals.get("market_cap"),
        "pe_ratio": fundamentals.get("pe_ratio"),
        "eps": fundamentals.get("eps"),
        "dividend_yield": fundamentals.get("dividend_yield"),
        "fifty_two_week_high": fundamentals.get("fifty_two_week_high"),
        "fifty_two_week_low": fundamentals.get("fifty_two_week_low"),
        "sector": fundamentals.get("sector"),
        "industry": fundamentals.get("industry"),
    }


def _fallback(stock_data: Dict[str, Any], error: str = "") -> FinancialMetricsAgentOutput:
    pe = stock_data.get("pe_ratio")
    price = stock_data.get("current_price")
    rating = "fairly valued"
    if pe is not None:
        if pe > 35:
            rating = "overvalued"
        elif pe < 15:
            rating = "undervalued"

    summary = (
        f"Price ${price}, P/E {pe}, market cap {stock_data.get('market_cap')}. "
        "Rule-based valuation estimate."
    )
    if error:
        summary = f"{summary} (MCP: {error})"

    return FinancialMetricsAgentOutput(
        valuation_rating=rating,
        analysis_summary=summary,
        key_metrics={
            "current_price": price,
            "pe_ratio": pe,
            "market_cap": stock_data.get("market_cap"),
            "fifty_two_week_high": stock_data.get("fifty_two_week_high"),
            "fifty_two_week_low": stock_data.get("fifty_two_week_low"),
        },
    )


async def _fetch_metrics_via_mcp(ticker: str) -> Dict[str, Any]:
    price_data = await call_tool("get_stock_price", {"ticker": ticker})
    fundamentals = await call_tool("get_stock_fundamentals", {"ticker": ticker})
    return _merge_mcp_stock_data(price_data, fundamentals)


async def analyze_financial_metrics(
    ticker: str, stock_data: Dict[str, Any] = None
) -> FinancialMetricsAgentOutput:
    try:
        mcp_error = ""
        metrics_data = stock_data or {}

        try:
            metrics_data = await _fetch_metrics_via_mcp(ticker)
        except MCPClientError as exc:
            mcp_error = str(exc)
            log_event(
                logger, logging.WARNING, "MCP financial tools failed", ticker=ticker, error=str(exc)
            )
            if not metrics_data:
                fb = _fallback({}, mcp_error)
                return FinancialMetricsAgentOutput(
                    **fb.model_dump(),
                    confidence_score=0.0,
                    error_message=str(exc),
                )

        fallback = _fallback(metrics_data, mcp_error)
        payload = json.dumps({"ticker": ticker, "stock_data": metrics_data}, default=str)

        result = await call_openai_json(
            system_prompt=(
                "You are a quantitative equity analyst. Assess valuation from the provided metrics. "
                "Respond ONLY with valid JSON: "
                '{"valuation_rating": "overvalued"|"fairly valued"|"undervalued", '
                '"analysis_summary": string, "key_metrics": object}'
            ),
            user_prompt=f"Analyze valuation for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )

        try:
            return FinancialMetricsAgentOutput.model_validate(result)
        except Exception:
            return fallback
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Financial metrics agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = _fallback(stock_data or {}, str(exc))
        return FinancialMetricsAgentOutput(
            **fb.model_dump(),
            confidence_score=0.0,
            error_message=str(exc),
        )
