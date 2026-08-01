"""OpenClaw browser control adapter with HTTP fallback.

When OPENCLAW_BROWSER_URL (+ optional token) is set, page content is retrieved
via the OpenClaw loopback browser control API. Otherwise callers should use
the httpx IR path in browser_tools.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx


def openclaw_enabled() -> bool:
    flag = os.getenv("BROWSER_MCP_ENABLED", "true").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(
        os.getenv("OPENCLAW_BROWSER_URL", "").strip()
        or os.getenv("OPENCLAW_USE_CLI", "").strip().lower() in {"1", "true", "yes", "on"}
    )


def _browser_base() -> str:
    return os.getenv("OPENCLAW_BROWSER_URL", "http://127.0.0.1:18791").rstrip("/")


def _auth_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
    password = os.getenv("OPENCLAW_GATEWAY_PASSWORD", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif password:
        headers["x-openclaw-password"] = password
    profile = os.getenv("OPENCLAW_BROWSER_PROFILE", "openclaw").strip()
    if profile:
        headers["x-openclaw-profile"] = profile
    return headers


def _profile_query() -> str:
    profile = os.getenv("OPENCLAW_BROWSER_PROFILE", "openclaw").strip() or "openclaw"
    return f"?profile={profile}"


def _extract_text_payload(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "content", "snapshot", "aria", "markdown", "body"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, dict):
                nested = _extract_text_payload(val)
                if nested:
                    return nested
        # Common nested shapes
        for key in ("result", "data", "page"):
            if key in data:
                nested = _extract_text_payload(data[key])
                if nested:
                    return nested
        return json.dumps(data)[:8000]
    if isinstance(data, list):
        parts = [_extract_text_payload(item) for item in data[:50]]
        return "\n".join(p for p in parts if p)
    return str(data)


def fetch_via_openclaw_http(url: str, timeout: float = 40.0) -> Optional[Dict[str, Any]]:
    """Navigate + snapshot via OpenClaw browser control HTTP API."""
    if not openclaw_enabled() and not os.getenv("OPENCLAW_BROWSER_URL", "").strip():
        return None
    if not os.getenv("OPENCLAW_BROWSER_URL", "").strip():
        # Explicit URL required for HTTP path; CLI path handled separately
        return None

    base = _browser_base()
    headers = _auth_headers()
    q = _profile_query()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme for browser fetch: {url}")

    with httpx.Client(timeout=timeout) as client:
        # Ensure browser is up (best-effort)
        try:
            client.post(f"{base}/start{q}&headless=true", headers=headers)
        except Exception:
            pass

        nav = client.post(
            f"{base}/navigate{q}",
            headers=headers,
            json={"url": url},
        )
        if nav.status_code >= 400:
            # Alternate tab-open API
            nav = client.post(
                f"{base}/tabs/open{q}",
                headers=headers,
                json={"url": url},
            )
        nav.raise_for_status()
        nav_data = {}
        try:
            nav_data = nav.json()
        except Exception:
            nav_data = {"raw": nav.text[:2000]}

        text = _extract_text_payload(nav_data)
        if len(text) < 200:
            snap = client.get(f"{base}/snapshot{q}&mode=efficient", headers=headers)
            snap.raise_for_status()
            try:
                snap_data = snap.json()
            except Exception:
                snap_data = {"raw": snap.text[:8000]}
            text = _extract_text_payload(snap_data) or text

    return {
        "url": url,
        "text": text[:12000],
        "provider": "openclaw_http",
        "title": (nav_data.get("title") if isinstance(nav_data, dict) else None) or "",
    }


def fetch_via_openclaw_cli(url: str, timeout: float = 45.0) -> Optional[Dict[str, Any]]:
    """Optional CLI path: openclaw browser open/snapshot --json."""
    use_cli = os.getenv("OPENCLAW_USE_CLI", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not use_cli:
        return None
    binary = os.getenv("OPENCLAW_CLI_PATH", "openclaw").strip() or "openclaw"
    if shutil.which(binary) is None and binary == "openclaw":
        return None

    profile = os.getenv("OPENCLAW_BROWSER_PROFILE", "openclaw").strip() or "openclaw"
    # Each CLI invocation pays node startup, so budget the shared timeout across
    # the open + snapshot pair instead of granting each the full allowance.
    deadline = time.monotonic() + timeout

    def _remaining(minimum: float = 3.0) -> float:
        return max(minimum, deadline - time.monotonic())

    try:
        # `start` is a no-op when the profile is already running. Headless is not
        # forced, since it conflicts with an already-running visible browser.
        subprocess.run(
            [binary, "browser", "--browser-profile", profile, "start"],
            capture_output=True,
            text=True,
            timeout=min(20.0, _remaining()),
            check=False,
        )
        open_proc = subprocess.run(
            [binary, "browser", "--browser-profile", profile, "--json", "open", url],
            capture_output=True,
            text=True,
            timeout=_remaining(),
            check=False,
        )
        snap_proc = subprocess.run(
            [
                binary,
                "browser",
                "--browser-profile",
                profile,
                "--json",
                "snapshot",
            ],
            capture_output=True,
            text=True,
            timeout=_remaining(),
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    text = ""
    title = ""
    for raw in (open_proc.stdout, snap_proc.stdout):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            text = raw[:12000]
            continue
        extracted = _extract_text_payload(data)
        if extracted:
            text = extracted
        if isinstance(data, dict) and data.get("title"):
            title = str(data.get("title"))

    if not text.strip():
        return None
    return {
        "url": url,
        "text": text[:12000],
        "provider": "openclaw_cli",
        "title": title,
    }


def snapshot_current_tab(profile: str = "user", timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """Snapshot the active tab of the user's REAL signed-in browser (no navigation).

    Used for authenticated pages (e.g. a brokerage positions page) we must
    never fetch ourselves — the user opens the page in their own browser and
    we read what's already on screen via OpenClaw's Chrome DevTools MCP
    'existing-session' attach. We never see credentials or the login flow.
    """
    base_url = os.getenv("OPENCLAW_BROWSER_URL", "").strip()
    if not base_url:
        return None

    base = base_url.rstrip("/")
    headers = _auth_headers()
    q = f"?profile={profile}"

    with httpx.Client(timeout=timeout) as client:
        snap = client.get(f"{base}/snapshot{q}&mode=efficient", headers=headers)
        snap.raise_for_status()
        try:
            snap_data = snap.json()
        except Exception:
            snap_data = {"raw": snap.text[:12000]}

    text = _extract_text_payload(snap_data)
    if not text.strip():
        return None

    tab_url = ""
    if isinstance(snap_data, dict):
        tab_url = snap_data.get("url") or snap_data.get("tabUrl") or ""

    return {
        "url": tab_url,
        "text": text[:16000],
        "provider": f"openclaw_existing_session:{profile}",
        "title": (snap_data.get("title") if isinstance(snap_data, dict) else "") or "",
    }


def fetch_page_via_openclaw(url: str, timeout: float = 40.0) -> Optional[Dict[str, Any]]:
    """Try HTTP control API first, then CLI if enabled."""
    try:
        result = fetch_via_openclaw_http(url, timeout=timeout)
        if result and (result.get("text") or "").strip():
            return result
    except Exception:
        pass
    try:
        result = fetch_via_openclaw_cli(url, timeout=timeout)
        if result and (result.get("text") or "").strip():
            return result
    except Exception:
        pass
    return None
