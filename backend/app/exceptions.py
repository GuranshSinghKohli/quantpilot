from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.mcp_client import MCPClientError, MCPUnavailableError


class StructuredErrorResponse(BaseModel):
    error: str
    detail: str
    timestamp: str


def _error_payload(error: str, detail: str) -> Dict[str, Any]:
    return StructuredErrorResponse(
        error=error,
        detail=detail,
        timestamp=datetime.now(timezone.utc).isoformat(),
    ).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                error=exc.detail if isinstance(exc.detail, str) else "request_error",
                detail=str(exc.detail),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                error="validation_error",
                detail=str(exc.errors()),
            ),
        )

    @app.exception_handler(MCPUnavailableError)
    async def mcp_unavailable_handler(request: Request, exc: MCPUnavailableError):
        return JSONResponse(
            status_code=503,
            content=_error_payload(error="mcp_unavailable", detail=str(exc)),
        )

    @app.exception_handler(MCPClientError)
    async def mcp_client_handler(request: Request, exc: MCPClientError):
        return JSONResponse(
            status_code=503,
            content=_error_payload(error="mcp_error", detail=str(exc)),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error_payload(error="internal_error", detail=str(exc)),
        )
