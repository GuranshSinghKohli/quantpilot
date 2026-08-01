"""MCP client helper — connects to quantpilot-finance MCP server via stdio."""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import BACKEND_DIR, REPO_ROOT
from app.observability.logger import get_logger, log_event

PROJECT_ROOT = REPO_ROOT
MCP_SERVER_SCRIPT = PROJECT_ROOT / "mcp_server" / "server.py"

_session = None
_lock = asyncio.Lock()
MCP_TIMEOUT_SECONDS = 5
MCP_BROWSER_TIMEOUT_SECONDS = int(os.getenv("MCP_BROWSER_TIMEOUT_SECONDS", "45"))
MCP_MAX_RETRIES = 2
MCP_RETRY_DELAY_SECONDS = 1
BROWSER_TOOL_NAMES = {
    "get_ir_materials",
    "fetch_browser_page",
    "get_shareholder_letter",
    "snapshot_active_browser_tab",
}

logger = get_logger("mcp_client")


class MCPClientError(Exception):
    """Raised when MCP server is unavailable or a tool call fails."""


class MCPUnavailableError(MCPClientError):
    """Raised when MCP server is unreachable after retries."""


class _MinimalMCPSession:
    """Minimal stdio JSON-RPC client for Python 3.9 fallback."""

    def __init__(self, process: asyncio.subprocess.Process):
        self._process = process
        self._next_id = 1

    async def _request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = MCP_TIMEOUT_SECONDS,
    ) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        line = json.dumps(payload) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        while True:
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=timeout,
            )
            if not response_line:
                raise MCPClientError("MCP server closed connection unexpectedly.")
            data = json.loads(response_line.decode())
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise MCPClientError(data["error"].get("message", "MCP tool error"))
            return data.get("result")

    async def initialize(self) -> None:
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "quantpilot-backend", "version": "1.0.0"},
            },
        )
        notify = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        self._process.stdin.write((json.dumps(notify) + "\n").encode())
        await self._process.stdin.drain()

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        timeout: float = MCP_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        content = result.get("content") or []
        if not content:
            return {}
        text = content[0].get("text", "{}")
        return json.loads(text)


class _OfficialMCPSession:
    """Official MCP SDK client session wrapper."""

    def __init__(self, session, stdio_context):
        self._session = session
        self._stdio_context = stdio_context

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        timeout: float = MCP_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        result = await asyncio.wait_for(
            self._session.call_tool(name, arguments=arguments),
            timeout=timeout,
        )
        for block in result.content:
            if hasattr(block, "text"):
                return json.loads(block.text)
        return {}


async def _connect_official() -> _OfficialMCPSession:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    backend_env = BACKEND_DIR / ".env"
    if backend_env.exists():
        from dotenv import load_dotenv

        load_dotenv(backend_env)

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    stdio = stdio_client(params)
    read, write = await stdio.__aenter__()
    session = ClientSession(read, write)
    await session.__aenter__()
    await asyncio.wait_for(session.initialize(), timeout=MCP_TIMEOUT_SECONDS)
    wrapper = _OfficialMCPSession(session, stdio)
    wrapper._session_cm = session
    wrapper._stdio_cm = stdio
    return wrapper


async def _connect_minimal() -> _MinimalMCPSession:
    env = os.environ.copy()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(MCP_SERVER_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    session = _MinimalMCPSession(process)
    await session.initialize()
    return session


async def get_mcp_session():
    global _session
    async with _lock:
        if _session is not None:
            return _session
        if not MCP_SERVER_SCRIPT.exists():
            raise MCPUnavailableError(
                f"MCP server not found at {MCP_SERVER_SCRIPT}. "
                "Start it with: python mcp_server/server.py"
            )
        try:
            if sys.version_info >= (3, 10):
                try:
                    _session = await asyncio.wait_for(
                        _connect_official(), timeout=MCP_TIMEOUT_SECONDS
                    )
                    return _session
                except ImportError:
                    pass
            _session = await asyncio.wait_for(
                _connect_minimal(), timeout=MCP_TIMEOUT_SECONDS
            )
            return _session
        except MCPClientError:
            raise
        except asyncio.TimeoutError as exc:
            raise MCPUnavailableError(
                "MCP server connection timed out after 5 seconds."
            ) from exc
        except Exception as exc:
            raise MCPUnavailableError(
                "MCP server is not running or failed to start. "
                "Start it first: python mcp_server/server.py"
            ) from exc


def _timeout_for_tool(tool_name: str, timeout: Optional[float] = None) -> float:
    if timeout is not None:
        return float(timeout)
    if tool_name in BROWSER_TOOL_NAMES:
        return float(MCP_BROWSER_TIMEOUT_SECONDS)
    return float(MCP_TIMEOUT_SECONDS)


def _max_retries_for_tool(tool_name: str) -> int:
    """Browser tools drive a real browser; a timeout means slow, not flaky.

    Retrying multiplies an already long wait by the attempt count and can push
    the whole analysis past the client's budget, so they get a single shot.
    """
    if tool_name in BROWSER_TOOL_NAMES:
        return 0
    return MCP_MAX_RETRIES


async def call_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    ticker = arguments.get("ticker", "")
    last_error: Optional[Exception] = None
    call_timeout = _timeout_for_tool(tool_name, timeout)
    max_retries = _max_retries_for_tool(tool_name)

    for attempt in range(max_retries + 1):
        start = time.perf_counter()
        try:
            session = await get_mcp_session()
            result = await session.call_tool(
                tool_name, arguments, timeout=call_timeout
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            log_event(
                logger,
                logging.INFO,
                "MCP tool call completed",
                tool_name=tool_name,
                ticker=ticker,
                success=True,
                duration_ms=duration_ms,
            )
            return result
        except MCPUnavailableError:
            raise
        except MCPClientError as exc:
            last_error = exc
        except asyncio.TimeoutError as exc:
            last_error = MCPUnavailableError(
                f"MCP tool '{tool_name}' timed out after {call_timeout}s."
            )
        except json.JSONDecodeError as exc:
            last_error = MCPClientError(f"MCP tool '{tool_name}' returned invalid JSON.")
        except Exception as exc:
            last_error = MCPClientError(
                f"MCP tool '{tool_name}' failed: {exc}"
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        log_event(
            logger,
            logging.WARNING if attempt < max_retries else logging.ERROR,
            "MCP tool call failed",
            tool_name=tool_name,
            ticker=ticker,
            success=False,
            duration_ms=duration_ms,
            attempt=attempt + 1,
            error=str(last_error),
        )
        if attempt < max_retries:
            await asyncio.sleep(MCP_RETRY_DELAY_SECONDS)

    if isinstance(last_error, MCPClientError):
        raise MCPUnavailableError(
            f"MCP server unreachable for tool '{tool_name}' after "
            f"{max_retries + 1} attempts: {last_error}"
        ) from last_error
    raise MCPUnavailableError(
        f"MCP server unreachable for tool '{tool_name}' after retries."
    )
