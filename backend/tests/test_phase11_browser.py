"""Phase 11 — Browser MCP / IR retrieval tools."""

import os

from mcp_server.tools.browser_tools import (
    _host_allowed,
    html_to_text,
    get_ir_materials,
)
from mcp_server.tools.openclaw_client import openclaw_enabled


def test_html_to_text_strips_tags():
    html = "<html><head><script>x()</script></head><body><h1>Hello</h1><p>World &amp; co</p></body></html>"
    text = html_to_text(html)
    assert "Hello" in text
    assert "World & co" in text
    assert "<script>" not in text


def test_host_allowlist():
    allowed = {"apple.com", "investor.apple.com"}
    assert _host_allowed("investor.apple.com", allowed)
    assert _host_allowed("www.sec.gov", allowed)
    assert _host_allowed("cdn.q4cdn.com", allowed)
    assert not _host_allowed("evil.example", allowed)


def test_get_ir_materials_disabled(monkeypatch):
    monkeypatch.setenv("BROWSER_MCP_ENABLED", "false")
    out = get_ir_materials("AAPL")
    assert out["enabled"] is False
    assert out["pages"] == []


def test_openclaw_enabled_respects_flag(monkeypatch):
    monkeypatch.setenv("BROWSER_MCP_ENABLED", "false")
    monkeypatch.setenv("OPENCLAW_BROWSER_URL", "http://127.0.0.1:18791")
    assert openclaw_enabled() is False
    monkeypatch.setenv("BROWSER_MCP_ENABLED", "true")
    assert openclaw_enabled() is True
