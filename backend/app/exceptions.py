from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import config as app_config
from app.mcp_client import MCPClientError, MCPUnavailableError
from app.security.request_context import get_request_id


class StructuredErrorResponse(BaseModel):
    error: str
    message: str
    detail: str
    timestamp: str
    request_id: str = ""


def _error_payload(error: str, detail: str, *, public: bool = True) -> Dict[str, Any]:
    safe_detail = (
        detail
        if public or app_config.ENVIRONMENT != "production"
        else "An unexpected error occurred."
    )
    return StructuredErrorResponse(
        error=error,
        message=safe_detail,
        detail=safe_detail,
        timestamp=datetime.now(timezone.utc).isoformat(),
        request_id=get_request_id(),
    ).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        code = exc.detail if isinstance(exc.detail, str) else "request_error"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(error=str(code), detail=str(exc.detail)),
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
            content=_error_payload(
                error="INVESTIGATION_FAILED",
                detail="Research service unavailable",
            ),
        )

    @app.exception_handler(MCPClientError)
    async def mcp_client_handler(request: Request, exc: MCPClientError):
        return JSONResponse(
            status_code=503,
            content=_error_payload(error="mcp_error", detail="Research tool failed"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                error="internal_error",
                detail=str(exc),
                public=False,
            ),
        )
