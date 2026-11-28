"""Phase 6 — Browser MCP contract.

LangGraph / investigation code calls these tools. OpenClaw stays behind this
module. httpx is tried first on allowlisted official domains; OpenClaw runs
only when a primary document cannot be retrieved from a structured page.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

from mcp_server.tools import browser_tools
from mcp_server.tools.openclaw_client import (
    fetch_page_via_openclaw,
    openclaw_enabled,
)

logger = logging.getLogger("quantpilot.browser_mcp")

_HREF_RE = re.compile(
    r"""<a[^>]+href=["']([^"']+)["'][^>]*>(.*?)</a>""",
    re.I | re.S,
)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_DATE_RE = re.compile(
    r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})|"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+20\d{2})|"
    r"(q[1-4]\s*fy?\s*20\d{2})",
    re.I,
)
_INJECTION_RE = re.compile(
    r"(ignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts))"
    r"|(reveal\s+(your\s+)?(api\s*key|secrets?|credentials?))"
    r"|(system\s+prompt)"
    r"|(\boverride\s+safety\b)",
    re.I,
)

_DOC_ALIASES = {
    "earnings_presentation": (
        "earnings presentation",
        "earnings slides",
        "earnings deck",
        "quarterly presentation",
        "q1",
        "q2",
        "q3",
        "q4",
    ),
    "investor_presentation": (
        "investor presentation",
        "investor slides",
        "investor deck",
        "ir presentation",
    ),
    "shareholder_letter": ("shareholder letter", "annual letter", "ceo letter"),
    "annual_report": ("annual report", "10-k", "form 10-k"),
    "company_announcement": ("press release", "announcement", "news release"),
}

_TRANSIENT_ERRORS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "503",
    "502",
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def max_pages_per_task() -> int:
    return max(1, min(8, _int_env("MAX_BROWSER_PAGES_PER_TASK", 4)))


def max_browser_retries() -> int:
    return max(0, min(3, _int_env("MAX_BROWSER_RETRIES", 1)))


def nav_timeout() -> float:
    return float(_int_env("BROWSER_NAV_TIMEOUT_SECONDS", 20))


def discovery_timeout() -> float:
    return float(_int_env("BROWSER_DISCOVERY_TIMEOUT_SECONDS", 30))


def extract_timeout() -> float:
    return float(_int_env("BROWSER_EXTRACT_TIMEOUT_SECONDS", 15))


def sanitize_web_text(text: str, max_chars: int = 8000) -> str:
    """Treat webpage text as untrusted data, never as instructions."""
    try:
        from app.security.untrusted import sanitize_untrusted_text

        return sanitize_untrusted_text(text, max_chars=max_chars)
    except Exception:
        cleaned: List[str] = []
        for line in (text or "").splitlines():
            if _INJECTION_RE.search(line):
                cleaned.append("[untrusted web instruction omitted]")
                continue
            cleaned.append(line)
        blob = "\n".join(cleaned)
        blob = _INJECTION_RE.sub("[untrusted web instruction omitted]", blob)
        return blob[:max_chars]


def official_hosts_for_ticker(ticker: str = "") -> Set[str]:
    hosts: Set[str] = set()
    symbol = (ticker or "").upper().strip()
    if symbol:
        _, allowed = browser_tools._candidate_urls(symbol)
        hosts.update(allowed)
    for suffix in browser_tools._ALWAYS_ALLOWED_HOST_SUFFIXES:
        hosts.add(suffix)
    hosts.update({"sec.gov", "www.sec.gov"})
    return hosts


def is_official_host(
    host: str,
    *,
    ticker: str = "",
    extra_allowed: Optional[Set[str]] = None,
) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    allowed = official_hosts_for_ticker(ticker)
    if extra_allowed:
        allowed |= {h.lower().strip(".") for h in extra_allowed}
    if browser_tools._host_allowed(host, allowed):
        return True
    if host.endswith(".gov"):
        return True
    bare = host[4:] if host.startswith("www.") else host
    return bare.startswith("investor.") or bare.startswith("ir.")


def validate_source_url(
    url: str,
    *,
    ticker: str = "",
    expected_host: str = "",
) -> Tuple[bool, str]:
    raw = (url or "").strip()
    if not raw:
        return False, "URL is missing."
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return False, "URL scheme is not http(s)."
    host = (parsed.hostname or "").lower()
    extra = {expected_host} if expected_host else None
    if expected_host:
        want = expected_host.lower().strip(".")
        if host != want and not host.endswith("." + want) and want not in host:
            if not is_official_host(host, ticker=ticker, extra_allowed=extra):
                return False, f"Host {host} is not the requested official domain."
    if not is_official_host(host, ticker=ticker, extra_allowed=extra):
        return False, f"Host {host} is not an official primary-source domain."
    return True, ""


def extract_links(html_or_text: str, base_url: str = "") -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen: Set[str] = set()

    def _add(href: str, title: str) -> None:
        href = (href or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            return
        if base_url:
            href = urljoin(base_url, href)
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            return
        key = href.split("#")[0]
        if key in seen:
            return
        seen.add(key)
        label = browser_tools.html_to_text(title or "")[:200]
        found.append({"url": key, "title": label or key})

    for match in _HREF_RE.finditer(html_or_text or ""):
        _add(match.group(1), match.group(2))
    for match in _MD_LINK_RE.finditer(html_or_text or ""):
        _add(match.group(2), match.group(1))
    if not found:
        for url in _BARE_URL_RE.findall(html_or_text or ""):
            _add(url, "")
    return found[:40]


def infer_document_type(title: str, url: str = "") -> str:
    blob = f"{title} {url}".lower()
    if any(tok in blob for tok in ("shareholder letter", "ceo letter")):
        return "shareholder_letter"
    if any(tok in blob for tok in ("annual report", "10-k", "form 10-k")):
        return "annual_report"
    if any(tok in blob for tok in ("investor day", "investor presentation")):
        return "investor_presentation"
    if any(
        tok in blob
        for tok in (
            "earnings presentation",
            "earnings slides",
            "earnings deck",
            "q1",
            "q2",
            "q3",
            "q4",
        )
    ) and any(tok in blob for tok in ("presentation", "slides", "deck", ".pdf")):
        return "earnings_presentation"
    if ".pdf" in blob or "presentation" in blob or "slides" in blob:
        return "investor_presentation"
    return "other"


def extract_date_hint(text: str) -> str:
    match = _DATE_RE.search(text or "")
    return match.group(0) if match else ""


def score_document(
    candidate: Dict[str, Any],
    *,
    document_type: str,
    ticker: str = "",
    company: str = "",
) -> int:
    title = str(candidate.get("title") or "")
    url = str(candidate.get("url") or "")
    blob = f"{title} {url}".lower()
    score = 0
    host = (urlparse(url).hostname or "").lower()
    if is_official_host(host, ticker=ticker):
        score += 40
    else:
        score -= 50
    wanted = (document_type or "investor_presentation").lower()
    aliases = _DOC_ALIASES.get(wanted, (wanted.replace("_", " "),))
    if any(alias in blob for alias in aliases):
        score += 25
    inferred = infer_document_type(title, url)
    if inferred == wanted:
        score += 20
    if wanted == "earnings_presentation" and inferred == "annual_report":
        score -= 20
    if wanted == "investor_presentation" and inferred == "annual_report":
        score -= 10
    if ticker and ticker.lower() in blob:
        score += 8
    if company and company.lower() in blob:
        score += 8
    if url.lower().endswith(".pdf") or ".pdf" in blob:
        score += 10
    if extract_date_hint(blob):
        score += 5
    if "fy2027" in blob or "2026" in blob or "2027" in blob:
        score += 3
    return score


def select_document(
    candidates: Sequence[Dict[str, Any]],
    *,
    document_type: str,
    ticker: str = "",
    company: str = "",
) -> Optional[Dict[str, Any]]:
    ranked = []
    for item in candidates:
        clone = dict(item)
        clone["score"] = score_document(
            clone, document_type=document_type, ticker=ticker, company=company
        )
        clone["document_type"] = infer_document_type(
            str(clone.get("title") or ""), str(clone.get("url") or "")
        )
        ranked.append(clone)
    wanted = (document_type or "investor_presentation").lower()
    aliases = _DOC_ALIASES.get(wanted, (wanted.replace("_", " "),))
    typed = []
    for row in ranked:
        blob = f"{row.get('title') or ''} {row.get('url') or ''}".lower()
        if row.get("document_type") == wanted or any(alias in blob for alias in aliases):
            typed.append(row)
    pool = typed or []
    pool.sort(key=lambda row: int(row.get("score") or 0), reverse=True)
    if not pool or int(pool[0].get("score") or 0) < 20:
        return None
    return pool[0]


def extract_pdf_text(payload: bytes, max_chars: int = 8000) -> Tuple[str, bool]:
    """Best-effort PDF text without adding a parser dependency.

    Returns (text, extracted). Never invents content.
    """
    if not payload:
        return "", False
    if payload[:5] != b"%PDF-":
        try:
            return payload.decode("utf-8", errors="ignore")[:max_chars], True
        except Exception:
            return "", False
    chunks: List[str] = []
    for match in re.finditer(rb"\((?:\\.|[^\\)]){4,}\)", payload):
        raw = match.group(0)[1:-1]
        try:
            text = raw.decode("latin-1", errors="ignore")
        except Exception:
            continue
        text = text.replace("\\n", " ").replace("\\r", " ")
        if sum(ch.isalpha() for ch in text) >= 8:
            chunks.append(text)
        if len(chunks) >= 80:
            break
    joined = sanitize_web_text(" ".join(chunks), max_chars=max_chars)
    if len(joined) < 40:
        return "", False
    return joined, True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields: Any) -> None:
    safe = {
        key: value
        for key, value in fields.items()
        if key
        not in {
            "token",
            "password",
            "cookie",
            "cookies",
            "authorization",
            "api_key",
            "secret",
        }
    }
    logger.info("%s %s", event, safe)


def _is_transient(message: str) -> bool:
    text = (message or "").lower()
    return any(token in text for token in _TRANSIENT_ERRORS)


def _fetch_official_page(
    url: str,
    *,
    ticker: str = "",
    allow_openclaw: bool = False,
) -> Dict[str, Any]:
    allowed = official_hosts_for_ticker(ticker)
    host = (urlparse(url).hostname or "").lower()
    if host:
        allowed.add(host)
    last_error = ""
    attempts = 1 + (max_browser_retries() if allow_openclaw else 0)
    for attempt in range(attempts):
        try:
            page = browser_tools.fetch_page(url, allowed_hosts=allowed)
            text = sanitize_web_text(str(page.get("text") or ""))
            if text.strip():
                return {
                    "url": page.get("url") or url,
                    "title": page.get("title") or "",
                    "text": text,
                    "provider": page.get("provider") or "httpx",
                    "error": "",
                }
            last_error = page.get("error") or "empty content"
        except Exception as exc:
            last_error = str(exc)
            if not _is_transient(last_error) or attempt + 1 >= attempts:
                break
            time.sleep(0.05)
    if allow_openclaw and openclaw_enabled():
        try:
            claw = fetch_page_via_openclaw(url, timeout=nav_timeout())
        except Exception as exc:
            claw = None
            last_error = str(exc)
        if claw and (claw.get("text") or "").strip():
            return {
                "url": claw.get("url") or url,
                "title": claw.get("title") or "",
                "text": sanitize_web_text(str(claw.get("text") or "")),
                "provider": claw.get("provider") or "openclaw",
                "error": "",
            }
    return {
        "url": url,
        "title": "",
        "text": "",
        "provider": "error",
        "error": last_error or "Page fetch failed",
    }


def browser_search(
    domain: str,
    query: str,
    max_results: int = 5,
    ticker: str = "",
) -> Dict[str, Any]:
    """Search an official site/domain. Does not search the open web."""
    started = time.perf_counter()
    _log("BROWSER_TASK_STARTED", tool="browser_search", domain=domain, ticker=ticker)
    host = (urlparse(domain if "://" in domain else f"https://{domain}").hostname or domain)
    host = (host or "").lower()
    ok, reason = validate_source_url(
        f"https://{host}/",
        ticker=ticker,
        expected_host=host,
    )
    if not ok:
        _log("BROWSER_SOURCE_REJECTED", domain=host, reason=reason)
        _log("BROWSER_TASK_FAILED", tool="browser_search", status="rejected")
        return {
            "ok": False,
            "error": reason,
            "candidates": [],
            "domain": host,
        }
    seed = domain if domain.startswith("http") else f"https://{host}/"
    page = _fetch_official_page(seed, ticker=ticker, allow_openclaw=False)
    if page.get("error") and not page.get("text"):
        _log("BROWSER_TASK_FAILED", tool="browser_search", status="fetch_failed")
        return {
            "ok": False,
            "error": page.get("error") or "Search page unavailable",
            "candidates": [],
            "domain": host,
        }
    q = (query or "").lower()
    matches = []
    for link in extract_links(page.get("text") or "", page.get("url") or seed):
        blob = f"{link.get('title')} {link.get('url')}".lower()
        if q and q not in blob:
            continue
        valid, _ = validate_source_url(link["url"], ticker=ticker, expected_host=host)
        if not valid:
            continue
        matches.append(link)
        if len(matches) >= max(1, min(int(max_results or 5), 10)):
            break
    latency_ms = int((time.perf_counter() - started) * 1000)
    _log(
        "BROWSER_TASK_COMPLETED",
        tool="browser_search",
        domain=host,
        status="ok",
        latency_ms=latency_ms,
        result_count=len(matches),
    )
    return {
        "ok": True,
        "domain": host,
        "query": query,
        "candidates": matches,
        "source_url": page.get("url") or seed,
        "provider": page.get("provider") or "httpx",
        "retrieved_at": _now_iso(),
        "error": "",
    }


def browser_open(url: str, ticker: str = "") -> Dict[str, Any]:
    started = time.perf_counter()
    _log("BROWSER_TASK_STARTED", tool="browser_open", url=url, ticker=ticker)
    ok, reason = validate_source_url(url, ticker=ticker)
    if not ok:
        _log("BROWSER_SOURCE_REJECTED", url=url, reason=reason)
        _log("BROWSER_TASK_FAILED", tool="browser_open", status="rejected")
        return {"ok": False, "error": reason, "url": url, "text": "", "title": ""}
    page = _fetch_official_page(url, ticker=ticker, allow_openclaw=openclaw_enabled())
    if not (page.get("text") or "").strip():
        _log("BROWSER_TASK_FAILED", tool="browser_open", status="empty")
        return {
            "ok": False,
            "error": page.get("error") or "Empty page content",
            "url": page.get("url") or url,
            "title": page.get("title") or "",
            "text": "",
            "provider": page.get("provider") or "error",
        }
    _log(
        "BROWSER_SOURCE_VALIDATED",
        url=page.get("url") or url,
        provider=page.get("provider"),
    )
    _log(
        "BROWSER_TASK_COMPLETED",
        tool="browser_open",
        status="ok",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return {
        "ok": True,
        "url": page.get("url") or url,
        "title": page.get("title") or "",
        "text": page.get("text") or "",
        "provider": page.get("provider") or "httpx",
        "retrieved_at": _now_iso(),
        "error": "",
    }


def browser_find_document(
    ticker: str,
    document_type: str = "investor_presentation",
    query: str = "",
    company: str = "",
    domain: str = "",
) -> Dict[str, Any]:
    """Find a specific official document. Not a general web search."""
    started = time.perf_counter()
    symbol = (ticker or "").upper().strip()
    wanted = (document_type or "investor_presentation").strip() or "investor_presentation"
    _log(
        "BROWSER_TASK_STARTED",
        tool="browser_find_document",
        ticker=symbol,
        document_type=wanted,
    )
    if not symbol:
        return {"ok": False, "error": "ticker is required", "candidates": []}

    seeds, allowed = browser_tools._candidate_urls(symbol)
    if domain:
        host = urlparse(domain if "://" in domain else f"https://{domain}").hostname
        if host and is_official_host(host, ticker=symbol):
            seeds = [domain if domain.startswith("http") else f"https://{host}/"] + seeds
            allowed.add(host.lower())
    seeds = seeds[: max_pages_per_task()]
    candidates: List[Dict[str, Any]] = []
    pages_visited = 0
    provider_used = "httpx"
    last_error = ""

    for seed in seeds:
        if pages_visited >= max_pages_per_task():
            break
        pages_visited += 1
        page = _fetch_official_page(seed, ticker=symbol, allow_openclaw=False)
        if page.get("error") and not page.get("text"):
            last_error = str(page.get("error"))
            continue
        provider_used = str(page.get("provider") or provider_used)
        for link in extract_links(page.get("text") or "", page.get("url") or seed):
            valid, _ = validate_source_url(link["url"], ticker=symbol)
            if not valid:
                _log("BROWSER_SOURCE_REJECTED", url=link["url"], reason="not official")
                continue
            title = link.get("title") or ""
            if query and query.lower() not in f"{title} {link['url']}".lower():
                if wanted.replace("_", " ") not in f"{title} {link['url']}".lower():
                    # keep type-matching links even if free-text query misses
                    if infer_document_type(title, link["url"]) == "other":
                        continue
            row = {
                "url": link["url"],
                "title": title,
                "published_at": extract_date_hint(f"{title} {link['url']}"),
                "source_page": page.get("url") or seed,
            }
            candidates.append(row)
            _log("BROWSER_DOCUMENT_FOUND", url=row["url"], title=title[:80])

    used_openclaw = False
    if not candidates and openclaw_enabled() and seeds:
        used_openclaw = True
        page = _fetch_official_page(seeds[0], ticker=symbol, allow_openclaw=True)
        provider_used = str(page.get("provider") or "openclaw")
        if page.get("text"):
            for link in extract_links(page.get("text") or "", page.get("url") or seeds[0]):
                valid, _ = validate_source_url(link["url"], ticker=symbol)
                if not valid:
                    continue
                candidates.append(
                    {
                        "url": link["url"],
                        "title": link.get("title") or "",
                        "published_at": extract_date_hint(
                            f"{link.get('title')} {link['url']}"
                        ),
                        "source_page": page.get("url") or seeds[0],
                    }
                )
                _log(
                    "BROWSER_DOCUMENT_FOUND",
                    url=link["url"],
                    title=(link.get("title") or "")[:80],
                    retrieval_method="openclaw",
                )
        elif page.get("error"):
            last_error = str(page.get("error"))

    selected = select_document(
        candidates,
        document_type=wanted,
        ticker=symbol,
        company=company,
    )
    if selected is None:
        _log("BROWSER_TASK_FAILED", tool="browser_find_document", status="not_found")
        return {
            "ok": False,
            "error": last_error or f"No official {wanted} found for {symbol}",
            "ticker": symbol,
            "document_type": wanted,
            "candidates": candidates[:8],
            "selected": None,
            "provider": provider_used,
            "used_openclaw": used_openclaw,
            "pages_visited": pages_visited,
            "retrieved_at": _now_iso(),
        }

    _log(
        "BROWSER_DOCUMENT_SELECTED",
        url=selected.get("url"),
        document_type=selected.get("document_type"),
        score=selected.get("score"),
    )
    downloaded = browser_download(str(selected["url"]), ticker=symbol)
    excerpt = downloaded.get("text") or ""
    if downloaded.get("ok") and excerpt:
        _log("BROWSER_CONTENT_EXTRACTED", url=selected.get("url"), chars=len(excerpt))
    elif downloaded.get("error"):
        excerpt = ""
    latency_ms = int((time.perf_counter() - started) * 1000)
    provider = downloaded.get("provider") or provider_used
    if used_openclaw and "openclaw" in str(provider):
        provider = "openclaw"
    _log(
        "BROWSER_TASK_COMPLETED",
        tool="browser_find_document",
        ticker=symbol,
        status="ok",
        latency_ms=latency_ms,
        retrieval_method="browser_mcp" if used_openclaw else provider,
    )
    return {
        "ok": True,
        "ticker": symbol,
        "document_type": wanted,
        "candidates": candidates[:8],
        "selected": selected,
        "url": selected.get("url"),
        "title": selected.get("title") or downloaded.get("title") or wanted,
        "published_at": selected.get("published_at") or "",
        "text": excerpt,
        "extraction_failed": bool(downloaded.get("extraction_failed")),
        "provider": provider,
        "used_openclaw": used_openclaw,
        "pages_visited": pages_visited,
        "retrieved_at": _now_iso(),
        "error": "",
    }


def browser_download(url: str, ticker: str = "") -> Dict[str, Any]:
    started = time.perf_counter()
    _log("BROWSER_TASK_STARTED", tool="browser_download", url=url, ticker=ticker)
    ok, reason = validate_source_url(url, ticker=ticker)
    if not ok:
        _log("BROWSER_SOURCE_REJECTED", url=url, reason=reason)
        return {"ok": False, "error": reason, "url": url, "text": ""}
    allowed = official_hosts_for_ticker(ticker)
    host = (urlparse(url).hostname or "").lower()
    if host:
        allowed.add(host)
    try:
        headers = {
            "User-Agent": browser_tools._user_agent(),
            "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
        }
        import httpx

        with httpx.Client(
            timeout=extract_timeout(), follow_redirects=True
        ) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            final_url = str(response.url)
            final_host = (urlparse(final_url).hostname or "").lower()
            if not is_official_host(final_host, ticker=ticker):
                return {
                    "ok": False,
                    "error": f"Redirect host not official: {final_host}",
                    "url": final_url,
                    "text": "",
                }
            ctype = (response.headers.get("content-type") or "").lower()
            body = response.content or b""
        title = ""
        extracted = ""
        extraction_failed = False
        if "pdf" in ctype or final_url.lower().endswith(".pdf") or body[:5] == b"%PDF-":
            extracted, ok_extract = extract_pdf_text(body)
            extraction_failed = not ok_extract
            title = os.path.basename(urlparse(final_url).path) or "document.pdf"
            _log("BROWSER_DOCUMENT_DOWNLOADED", url=final_url, content_type="pdf")
        else:
            text = body.decode("utf-8", errors="ignore")
            extracted = sanitize_web_text(browser_tools.html_to_text(text))
            match = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.I | re.S)
            title = browser_tools.html_to_text(match.group(1)) if match else ""
            if len(extracted) < 20:
                extraction_failed = True
        _log(
            "BROWSER_TASK_COMPLETED",
            tool="browser_download",
            status="ok" if extracted or not extraction_failed else "extract_failed",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return {
            "ok": bool(extracted) or not extraction_failed,
            "url": final_url,
            "title": title,
            "text": extracted,
            "extraction_failed": extraction_failed,
            "provider": "httpx",
            "retrieved_at": _now_iso(),
            "error": "PDF text could not be extracted" if extraction_failed and not extracted else "",
        }
    except Exception as exc:
        message = str(exc)
        _log("BROWSER_TASK_FAILED", tool="browser_download", error=message[:200])
        return {
            "ok": False,
            "error": message,
            "url": url,
            "text": "",
            "extraction_failed": True,
            "provider": "error",
        }


def browser_extract(
    text: str,
    objective: str,
    source_url: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """Extract a relevant window from already-retrieved text. Data only."""
    started = time.perf_counter()
    _log("BROWSER_TASK_STARTED", tool="browser_extract", objective=objective[:80])
    blob = sanitize_web_text(text or "", max_chars=12000)
    if not blob.strip():
        return {
            "ok": False,
            "error": "Empty content; nothing to extract",
            "text": "",
            "source_url": source_url,
        }
    terms = [t for t in re.split(r"\s+", (objective or "").lower()) if len(t) > 3]
    lower = blob.lower()
    best_idx = 0
    for term in terms:
        idx = lower.find(term)
        if idx >= 0:
            best_idx = idx
            break
    start = max(0, best_idx - 280)
    excerpt = blob[start : start + 1600].strip()
    _log(
        "BROWSER_CONTENT_EXTRACTED",
        chars=len(excerpt),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    _log("BROWSER_TASK_COMPLETED", tool="browser_extract", status="ok")
    return {
        "ok": True,
        "title": title,
        "source_url": source_url,
        "objective": objective,
        "text": excerpt,
        "retrieved_at": _now_iso(),
        "error": "",
    }
