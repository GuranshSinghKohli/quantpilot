#!/usr/bin/env python3
"""QuantPilot finance MCP server — stdio transport."""

import sys
from pathlib import Path

# Allow imports from project root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "backend" / ".env")

from mcp_server.tools import browser_tools, sec_tools, stock_tools

HANDLERS = {
    "get_stock_price": stock_tools.get_stock_price,
    "get_stock_fundamentals": stock_tools.get_stock_fundamentals,
    "get_stock_news": stock_tools.get_stock_news,
    "get_price_history": stock_tools.get_price_history,
    "get_recent_filings": sec_tools.get_recent_filings,
    "get_company_facts": sec_tools.get_company_facts,
    "get_ir_materials": browser_tools.get_ir_materials,
    "fetch_browser_page": browser_tools.fetch_browser_page,
    "get_shareholder_letter": browser_tools.get_shareholder_letter,
    "snapshot_active_browser_tab": browser_tools.snapshot_active_browser_tab,
}


def _run_official_mcp() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("quantpilot-finance")

    @mcp.tool()
    def get_stock_price(ticker: str) -> dict:
        """Fetch current stock price, previous close, change %, and volume."""
        return stock_tools.get_stock_price(ticker)

    @mcp.tool()
    def get_stock_fundamentals(ticker: str) -> dict:
        """Fetch market cap, P/E, EPS, dividend yield, 52w range, sector, industry."""
        return stock_tools.get_stock_fundamentals(ticker)

    @mcp.tool()
    def get_stock_news(ticker: str, limit: int = 5) -> dict:
        """Fetch recent news headlines for a ticker."""
        return stock_tools.get_stock_news(ticker, limit=limit)

    @mcp.tool()
    def get_price_history(ticker: str, period: str = "1mo") -> dict:
        """Fetch OHLCV price history for a ticker."""
        return stock_tools.get_price_history(ticker, period=period)

    @mcp.tool()
    def get_recent_filings(ticker: str, limit: int = 3) -> dict:
        """Fetch recent SEC EDGAR filings for a ticker."""
        return sec_tools.get_recent_filings(ticker, limit=limit)

    @mcp.tool()
    def get_company_facts(ticker: str) -> dict:
        """Fetch SEC company facts (CIK, name, SIC, state)."""
        return sec_tools.get_company_facts(ticker)

    @mcp.tool()
    def get_ir_materials(ticker: str, max_pages: int = 2) -> dict:
        """Fetch investor-relations pages via OpenClaw browser or allowlisted HTTP."""
        return browser_tools.get_ir_materials(ticker, max_pages=max_pages)

    @mcp.tool()
    def fetch_browser_page(url: str) -> dict:
        """Fetch a single allowlisted IR/public page (OpenClaw when configured)."""
        return browser_tools.fetch_browser_page(url)

    @mcp.tool()
    def get_shareholder_letter(ticker: str) -> dict:
        """Best-effort shareholder / annual letter excerpt from IR materials."""
        return browser_tools.get_shareholder_letter(ticker)

    @mcp.tool()
    def snapshot_active_browser_tab() -> dict:
        """Read the active tab of the user's real signed-in browser via OpenClaw."""
        return browser_tools.snapshot_active_browser_tab()

    mcp.run(transport="stdio")


def _run_fallback_stdio() -> None:
    from mcp_server.stdio_transport import run_stdio_server

    run_stdio_server(HANDLERS)


if __name__ == "__main__":
    if sys.version_info >= (3, 10):
        try:
            _run_official_mcp()
        except ImportError:
            _run_fallback_stdio()
    else:
        _run_fallback_stdio()
