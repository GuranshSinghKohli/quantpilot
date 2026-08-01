"""Browser / IR retrieval tools for Phase 11.

Primary path: OpenClaw browser control (when configured).
Fallback: allowlisted httpx fetch of investor-relations pages.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import yfinance as yf

from mcp_server.tools.openclaw_client import snapshot_current_tab
from mcp_server.tools.utils import validate_ticker

# Hosts commonly used for IR portals / document CDNs
_ALWAYS_ALLOWED_HOST_SUFFIXES = (
    "sec.gov",
    "q4cdn.com",
    "q4web.com",
    "investorroom.com",
    "corporate-ir.net",
)

_KNOWN_IR_URLS: Dict[str, List[str]] = {
    "AAPL": ["https://investor.apple.com/"],
    "MSFT": ["https://www.microsoft.com/en-us/investor"],
    "GOOGL": ["https://abc.xyz/investor/"],
    "GOOG": ["https://abc.xyz/investor/"],
    "AMZN": ["https://ir.aboutamazon.com/"],
    "META": ["https://investor.atmeta.com/"],
    "NVDA": ["https://investor.nvidia.com/home/default.aspx"],
    "TSLA": ["https://ir.tesla.com/"],
    "JPM": ["https://www.jpmorganchase.com/ir"],
    "V": ["https://investor.visa.com/"],
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(
    r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.I | re.S
)


def browser_tools_enabled() -> bool:
    return os.getenv("BROWSER_MCP_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _user_agent() -> str:
    return os.getenv(
        "SEC_EDGAR_USER_AGENT",
        "QuantPilot/1.0 (contact@example.com)",
    )


def html_to_text(html: str, max_chars: int = 12000) -> str:
    cleaned = _SCRIPT_RE.sub(" ", html or "")
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = _WS_RE.sub(" ", cleaned).strip()
    return text[:max_chars]


def _host_allowed(host: str, allowed_hosts: Set[str]) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    if host in allowed_hosts:
        return True
    for suffix in _ALWAYS_ALLOWED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    for allowed in allowed_hosts:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _company_website(ticker: str) -> Optional[str]:
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    site = info.get("website") or info.get("irWebsite")
    if not site:
        return None
    if not str(site).startswith("http"):
        site = "https://" + str(site)
    return str(site).rstrip("/")


def _candidate_urls(ticker: str) -> Tuple[List[str], Set[str]]:
    symbol = ticker.upper().strip()
    urls: List[str] = list(_KNOWN_IR_URLS.get(symbol, []))
    allowed: Set[str] = set()

    for u in urls:
        host = urlparse(u).hostname
        if host:
            allowed.add(host.lower())

    website = _company_website(symbol)
    if website:
        parsed = urlparse(website)
        host = (parsed.hostname or "").lower()
        if host:
            allowed.add(host)
            # strip www.
            bare = host[4:] if host.startswith("www.") else host
            allowed.add(bare)
            allowed.add(f"investor.{bare}")
            allowed.add(f"ir.{bare}")
            base = f"{parsed.scheme}://{parsed.netloc}"
            urls.extend(
                [
                    f"{base}/investor",
                    f"{base}/investors",
                    f"{base}/investor-relations",
                    f"{base}/investor-relations/default.aspx",
                    f"https://investor.{bare}/",
                    f"https://ir.{bare}/",
                ]
            )

    # Dedupe preserve order
    seen: Set[str] = set()
    ordered: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered, allowed


def _fetch_http(url: str, allowed_hosts: Set[str], timeout: float = 20.0) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {url}")
    host = (parsed.hostname or "").lower()
    if not _host_allowed(host, allowed_hosts):
        raise ValueError(f"URL host not allowlisted for IR fetch: {host}")

    headers = {
        "User-Agent": _user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        final_url = str(response.url)
        final_host = (urlparse(final_url).hostname or "").lower()
        if not _host_allowed(final_host, allowed_hosts):
            raise ValueError(f"Redirect host not allowlisted: {final_host}")
        ctype = (response.headers.get("content-type") or "").lower()
        body = response.text
        if "html" in ctype or body.lstrip().startswith("<"):
            text = html_to_text(body)
        else:
            text = body[:12000]

    title_match = re.search(r"<title[^>]*>(.*?)</title>", body or "", re.I | re.S)
    title = html_to_text(title_match.group(1)) if title_match else ""
    return {
        "url": final_url,
        "text": text,
        "provider": "httpx",
        "title": title,
        "status_code": response.status_code,
    }


def fetch_page(url: str, allowed_hosts: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Fetch a public IR/company page via allowlisted httpx only.

    Deliberately does **not** drive OpenClaw to open/navigate company URLs —
    that was spawning Chrome tabs/windows during analysis. OpenClaw is reserved
    for read-only snapshots of the user's already-open tab (portfolio sync).
    """
    if not browser_tools_enabled():
        return {
            "url": url,
            "text": "",
            "provider": "disabled",
            "title": "",
            "error": "BROWSER_MCP_ENABLED is false",
        }

    hosts = allowed_hosts or set()
    host = (urlparse(url).hostname or "").lower()
    if host:
        hosts = set(hosts)
        hosts.add(host)

    return _fetch_http(url, hosts)


def snapshot_active_browser_tab() -> Dict[str, Any]:
    """MCP tool: read whatever page is open in the user's real signed-in browser.

    For authenticated pages we must never fetch ourselves (e.g. a brokerage
    positions page). The user opens the page in their own Chrome; OpenClaw's
    'existing-session' driver reads what's on screen. Credentials/login are
    never touched by this tool. Returns enabled=False with a clear message
    when OpenClaw isn't configured, so callers can fall back to paste-based
    import.
    """
    if not browser_tools_enabled():
        return {
            "enabled": False,
            "provider": "disabled",
            "url": "",
            "text": "",
            "error": "BROWSER_MCP_ENABLED is false",
        }
    if not os.getenv("OPENCLAW_BROWSER_URL", "").strip():
        return {
            "enabled": False,
            "provider": "not_configured",
            "url": "",
            "text": "",
            "error": (
                "OpenClaw is not configured (OPENCLAW_BROWSER_URL unset). "
                "Paste the positions page text instead, or set up OpenClaw's "
                "Gateway and 'existing-session' Chrome DevTools attach."
            ),
        }
    try:
        profile = os.getenv("OPENCLAW_BROWSER_PROFILE_EXISTING", "user")
        snap = snapshot_current_tab(profile=profile)
        if not snap:
            return {
                "enabled": True,
                "provider": "openclaw",
                "url": "",
                "text": "",
                "error": "OpenClaw returned no snapshot. Is a tab open and attached?",
            }
        return {
            "enabled": True,
            "provider": snap.get("provider", "openclaw"),
            "url": snap.get("url", ""),
            "title": snap.get("title", ""),
            "text": snap.get("text", ""),
            "error": "",
        }
    except Exception as exc:
        return {
            "enabled": True,
            "provider": "openclaw",
            "url": "",
            "text": "",
            "error": f"OpenClaw snapshot failed: {exc}",
        }


def fetch_browser_page(url: str) -> Dict[str, Any]:
    """MCP tool: fetch one allowlisted IR / public page."""
    if not url or not str(url).strip():
        raise ValueError("url is required")
    raw = str(url).strip()
    host = (urlparse(raw).hostname or "").lower()
    allowed = {host} if host else set()
    # Always permit SEC / IR CDNs
    for suffix in _ALWAYS_ALLOWED_HOST_SUFFIXES:
        allowed.add(suffix)
    try:
        page = fetch_page(raw, allowed_hosts=allowed)
        return {
            "url": page.get("url") or raw,
            "title": page.get("title") or "",
            "provider": page.get("provider") or "unknown",
            "text_excerpt": (page.get("text") or "")[:8000],
            "char_count": len(page.get("text") or ""),
            "error": page.get("error") or "",
        }
    except Exception as exc:
        return {
            "url": raw,
            "title": "",
            "provider": "error",
            "text_excerpt": "",
            "char_count": 0,
            "error": str(exc),
        }


def _normalize_symbol(ticker: str) -> str:
    symbol = (ticker or "").upper().strip()
    if not symbol or len(symbol) > 10 or not symbol.isalnum():
        raise ValueError(f"Invalid ticker symbol: '{ticker}'")
    return symbol


def get_ir_materials(ticker: str, max_pages: int = 2) -> Dict[str, Any]:
    """MCP tool: discover and fetch investor-relations materials for a ticker."""
    if not browser_tools_enabled():
        return {
            "ticker": (ticker or "").upper().strip(),
            "enabled": False,
            "provider": "disabled",
            "pages": [],
            "sources": [],
            "excerpt": "",
            "error": "BROWSER_MCP_ENABLED is false",
        }

    # Prefer lightweight normalize so IR works even if Yahoo is down.
    try:
        symbol = validate_ticker(ticker)
    except Exception:
        symbol = _normalize_symbol(ticker)
    candidates, allowed = _candidate_urls(symbol)
    limit = max(1, min(int(max_pages or 2), 3))
    pages: List[Dict[str, Any]] = []
    errors: List[str] = []

    for url in candidates:
        if len(pages) >= limit:
            break
        try:
            page = fetch_page(url, allowed_hosts=allowed)
            text = (page.get("text") or "").strip()
            if len(text) < 120:
                errors.append(f"thin content: {url}")
                continue
            final_url = page.get("url") or url
            if any(p.get("url") == final_url for p in pages):
                continue
            pages.append(
                {
                    "url": final_url,
                    "title": page.get("title") or "",
                    "provider": page.get("provider") or "unknown",
                    "text_excerpt": text[:6000],
                    "char_count": len(text),
                }
            )
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    # SEC EDGAR company filings browse as always-on earnings-adjacent fallback
    if len(pages) < limit:
        try:
            from mcp_server.tools.utils import ticker_to_cik

            try:
                cik, _ = ticker_to_cik(symbol)
            except Exception:
                # Lightweight CIK lookup without Yahoo validation
                import httpx as _httpx
                from mcp_server.tools.utils import SEC_TICKERS_URL, _sec_headers

                with _httpx.Client(timeout=20.0) as client:
                    data = client.get(SEC_TICKERS_URL, headers=_sec_headers()).json()
                cik = None
                for entry in data.values():
                    if entry.get("ticker", "").upper() == symbol:
                        cik = str(entry["cik_str"]).zfill(10)
                        break
                if not cik:
                    raise ValueError(f"No CIK for {symbol}")

            sec_url = (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type=8-K&count=5"
            )
            page = fetch_page(sec_url, allowed_hosts=allowed | {"sec.gov", "www.sec.gov"})
            text = (page.get("text") or "").strip()
            if len(text) >= 120 and not any(p.get("url") == (page.get("url") or sec_url) for p in pages):
                pages.append(
                    {
                        "url": page.get("url") or sec_url,
                        "title": page.get("title") or "SEC EDGAR 8-K search",
                        "provider": page.get("provider") or "httpx",
                        "text_excerpt": text[:6000],
                        "char_count": len(text),
                    }
                )
        except Exception as exc:
            errors.append(f"sec_fallback: {exc}")

    excerpts = [p["text_excerpt"] for p in pages if p.get("text_excerpt")]
    providers = sorted({p.get("provider") for p in pages if p.get("provider")})
    return {
        "ticker": symbol,
        "enabled": True,
        "provider": ",".join(providers) if providers else "none",
        "pages": pages,
        "sources": [p.get("url") for p in pages if p.get("url")],
        "excerpt": "\n\n---\n\n".join(excerpts)[:10000],
        "candidates_tried": candidates[:8],
        "error": "; ".join(errors[:5]) if not pages else "",
    }


def get_shareholder_letter(ticker: str) -> Dict[str, Any]:
    """MCP tool: best-effort shareholder letter / annual letter from IR materials."""
    materials = get_ir_materials(ticker, max_pages=2)
    pages = materials.get("pages") or []
    keywords = ("shareholder", "letter", "annual", "ceo", "chairman", "dear")
    best = None
    best_score = -1
    for page in pages:
        blob = f"{page.get('title', '')} {page.get('text_excerpt', '')}".lower()
        score = sum(1 for k in keywords if k in blob)
        if score > best_score:
            best_score = score
            best = page

    if not best:
        return {
            "ticker": materials.get("ticker"),
            "found": False,
            "url": "",
            "provider": materials.get("provider"),
            "text_excerpt": "",
            "error": materials.get("error") or "No IR pages retrieved",
        }

    return {
        "ticker": materials.get("ticker"),
        "found": True,
        "url": best.get("url") or "",
        "title": best.get("title") or "",
        "provider": best.get("provider") or "",
        "text_excerpt": (best.get("text_excerpt") or "")[:8000],
        "relevance_score": best_score,
        "error": "",
    }
