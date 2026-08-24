from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import MAX_REQUEST_BODY_BYTES, get_allowed_origins
from app.db.session import engine, init_db
from app.exceptions import register_exception_handlers
from app.jobs.scheduler import shutdown_scheduler, start_scheduler
from app.models.schemas import HealthResponse, ReadyResponse
from app.observability.instrumentation import (
    get_observability_status,
    init_observability,
)
from app.observability.logger import get_logger, log_event
from app.runtime import is_draining, set_draining
from app.security.request_context import new_request_id, set_request_id
from app.mcp_client import close_mcp_session
from app.services import redis_store
from app.services.readiness import readiness_payload
from app.routers import (
    alerts,
    analysis,
    auth,
    dashboard,
    filings,
    investigations,
    memory,
    observability,
    portfolio,
    recommendations,
    stocks,
    watchlist,
)

request_logger = get_logger("api")

_EXPENSIVE_PREFIXES = (
    "/api/investigations/investigate",
    "/api/analysis/",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_scheduler()
    log_event(request_logger, logging.INFO, "APPLICATION_STARTED", event="APPLICATION_STARTED")
    try:
        yield
    finally:
        set_draining(True)
        shutdown_scheduler()
        redis_store.release_lock("investigation_sweep")
        redis_store.close_redis()
        await close_mcp_session()
        engine.dispose()
        log_event(request_logger, logging.INFO, "APPLICATION_STOPPED", event="APPLICATION_STOPPED")


app = FastAPI(
    title="QuantPilot API",
    description=(
        "QuantPilot API — autonomous investigation engine with JWT auth, "
        "Evidence Ledger investigations, company memory, alerts, and a multi-agent research pipeline. "
        "Interactive OpenAPI: /docs"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Phase 12 — LangSmith / Sentry / OpenTelemetry (all optional via env)
init_observability(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(filings.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(investigations.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(observability.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or new_request_id()
    set_request_id(request_id)
    if is_draining() and request.method in {"POST", "PUT", "PATCH"} and any(
        request.url.path.startswith(p) for p in _EXPENSIVE_PREFIXES
    ):
        return JSONResponse(
            status_code=503,
            content={
                "error": "SERVICE_DRAINING",
                "message": "Server is shutting down",
                "detail": "Server is shutting down",
                "request_id": request_id,
            },
            headers={"X-Request-Id": request_id},
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "PAYLOAD_TOO_LARGE",
                        "message": "Request body exceeds the allowed size",
                        "detail": "Request body exceeds the allowed size",
                        "request_id": request_id,
                    },
                    headers={"X-Request-Id": request_id},
                )
        except ValueError:
            pass
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event(
            request_logger,
            logging.ERROR,
            "APPLICATION_ERROR",
            event="APPLICATION_ERROR",
            route=request.url.path,
            method=request.method,
        )
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Request-Id"] = request_id
    if request.url.path.startswith("/api"):
        log_event(
            request_logger,
            logging.INFO,
            "API route call",
            route=request.url.path,
            method=request.method,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
    return response


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    obs = get_observability_status()
    ready = readiness_payload()
    return HealthResponse(
        status="ok",
        langsmith=bool(obs.get("langsmith")),
        sentry=bool(obs.get("sentry")),
        otel=bool(obs.get("otel")),
        version=app.version or "1.0.0",
        application=str(ready["application"]),
        database=str(ready["database"]),
        redis=str(ready["redis"]),
        draining=bool(ready["draining"]),
    )


@app.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
    payload = readiness_payload()
    return ReadyResponse(**payload)
