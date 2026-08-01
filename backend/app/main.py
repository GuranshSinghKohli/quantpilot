from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_allowed_origins
from app.db.session import init_db
from app.exceptions import register_exception_handlers
from app.jobs.scheduler import shutdown_scheduler, start_scheduler
from app.models.schemas import HealthResponse
from app.observability.instrumentation import init_observability
from app.observability.logger import get_logger, log_event
from app.routers import (
    alerts,
    analysis,
    auth,
    filings,
    memory,
    observability,
    portfolio,
    recommendations,
    stocks,
    watchlist,
)

request_logger = get_logger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_scheduler()
    log_event(request_logger, logging.INFO, "QuantPilot API started")
    yield
    shutdown_scheduler()


app = FastAPI(
    title="QuantPilot API",
    description="QuantPilot API, production quant research copilot with multi-agent pipeline",
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
app.include_router(watchlist.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(observability.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
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
    return HealthResponse(status="ok")
