import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent


def find_repo_root() -> Path:
    """Resolve monorepo root whether cwd is repo root or backend/ (e.g. Railway)."""
    if (_REPO_ROOT / "mcp_server" / "server.py").exists():
        return _REPO_ROOT
    if (_BACKEND_DIR / "mcp_server" / "server.py").exists():
        return _BACKEND_DIR
    env_root = os.getenv("QUANTPILOT_ROOT")
    if env_root:
        return Path(env_root)
    return _REPO_ROOT


REPO_ROOT = find_repo_root()
BACKEND_DIR = REPO_ROOT / "backend" if (REPO_ROOT / "backend").is_dir() else _BACKEND_DIR

_env_file = BACKEND_DIR / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv()

_DEFAULT_SQLITE = f"sqlite:///{(BACKEND_DIR / 'data' / 'quantpilot.db').as_posix()}"


def _normalize_database_url(raw: str) -> str:
    """Railway often provides postgres://; SQLAlchemy 2 expects postgresql://."""
    if raw.startswith("postgres://"):
        return "postgresql://" + raw[len("postgres://") :]
    return raw


DATABASE_URL = _normalize_database_url(
    os.getenv("DATABASE_URL", _DEFAULT_SQLITE).strip() or _DEFAULT_SQLITE
)

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-change-me-quantpilot")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))

# Phase 8 — daily portfolio briefings
BRIEFING_ENABLED = os.getenv("BRIEFING_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BRIEFING_HOUR_UTC = int(os.getenv("BRIEFING_HOUR_UTC", "12"))
BRIEFING_MINUTE_UTC = int(os.getenv("BRIEFING_MINUTE_UTC", "0"))
BRIEFING_MAX_HOLDINGS = int(os.getenv("BRIEFING_MAX_HOLDINGS", "12"))

# Phase 9 - Redis + smart alerts
REDIS_URL = os.getenv("REDIS_URL", "").strip()
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALERT_INTERVAL_MINUTES = int(os.getenv("ALERT_INTERVAL_MINUTES", "15"))
QUOTE_CACHE_TTL_SECONDS = int(os.getenv("QUOTE_CACHE_TTL_SECONDS", "60"))

# Browser MCP / OpenClaw IR retrieval
BROWSER_MCP_ENABLED = os.getenv("BROWSER_MCP_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OPENCLAW_BROWSER_URL = os.getenv("OPENCLAW_BROWSER_URL", "").strip()
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "").strip()
OPENCLAW_BROWSER_PROFILE = os.getenv("OPENCLAW_BROWSER_PROFILE", "openclaw").strip()
MCP_BROWSER_TIMEOUT_SECONDS = int(os.getenv("MCP_BROWSER_TIMEOUT_SECONDS", "45"))
MAX_BROWSER_TASKS_PER_INVESTIGATION = int(
    os.getenv("MAX_BROWSER_TASKS_PER_INVESTIGATION", "2")
)
MAX_BROWSER_PAGES_PER_TASK = int(os.getenv("MAX_BROWSER_PAGES_PER_TASK", "4"))
MAX_BROWSER_RETRIES = int(os.getenv("MAX_BROWSER_RETRIES", "1"))
BROWSER_NAV_TIMEOUT_SECONDS = int(os.getenv("BROWSER_NAV_TIMEOUT_SECONDS", "20"))
BROWSER_DISCOVERY_TIMEOUT_SECONDS = int(
    os.getenv("BROWSER_DISCOVERY_TIMEOUT_SECONDS", "30")
)
BROWSER_EXTRACT_TIMEOUT_SECONDS = int(os.getenv("BROWSER_EXTRACT_TIMEOUT_SECONDS", "15"))

# PRD v3 Phase 11 — scheduled investigation sweep + trigger thresholds
# Phase 9 — default-on. Tests/CI set this false; QUANTPILOT_LIGHTWEIGHT also disables.
INVESTIGATIONS_SWEEP_ENABLED = os.getenv(
    "INVESTIGATIONS_SWEEP_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
INVESTIGATION_SWEEP_INTERVAL_MINUTES = int(
    os.getenv("INVESTIGATION_SWEEP_INTERVAL_MINUTES", "30")
)
INVESTIGATION_SWEEP_MAX_PER_RUN = int(os.getenv("INVESTIGATION_SWEEP_MAX_PER_RUN", "5"))
INVESTIGATION_COOLDOWN_HOURS = float(os.getenv("INVESTIGATION_COOLDOWN_HOURS", "6"))
INVESTIGATION_VOL_Z_THRESHOLD = float(os.getenv("INVESTIGATION_VOL_Z_THRESHOLD", "1.5"))
INVESTIGATION_IDIO_PCT = float(os.getenv("INVESTIGATION_IDIO_PCT", "1.0"))
INVESTIGATION_BENCHMARK = os.getenv("INVESTIGATION_BENCHMARK", "SPY").strip() or "SPY"
INVESTIGATION_PRICE_MOVE_THRESHOLD = float(
    os.getenv("INVESTIGATION_PRICE_MOVE_THRESHOLD", "2.5")
)
# Pre-investigation gate (distinct from post-investigation notify bar).
INVESTIGATION_MATERIALITY_THRESHOLD = float(
    os.getenv("INVESTIGATION_MATERIALITY_THRESHOLD", "40")
)
# Points added at 100% portfolio weight (0.22 weight → 22 points at default 100).
INVESTIGATION_EXPOSURE_WEIGHT = float(os.getenv("INVESTIGATION_EXPOSURE_WEIGHT", "100"))
INVESTIGATION_EARNINGS_LOOKAHEAD_DAYS = int(
    os.getenv("INVESTIGATION_EARNINGS_LOOKAHEAD_DAYS", "2")
)
INVESTIGATION_EARNINGS_LOOKBACK_DAYS = int(
    os.getenv("INVESTIGATION_EARNINGS_LOOKBACK_DAYS", "1")
)
INVESTIGATION_FILING_LOOKBACK_DAYS = int(
    os.getenv("INVESTIGATION_FILING_LOOKBACK_DAYS", "3")
)
INVESTIGATION_EVENT_BUCKET_HOURS = int(
    os.getenv("INVESTIGATION_EVENT_BUCKET_HOURS", "6")
)
INVESTIGATION_REOPEN_MOVE_DELTA = float(
    os.getenv("INVESTIGATION_REOPEN_MOVE_DELTA", "3.0")
)
MAX_INVESTIGATION_ITERATIONS = int(os.getenv("MAX_INVESTIGATION_ITERATIONS", "2"))
MAX_CHALLENGE_LOOPS = int(os.getenv("MAX_CHALLENGE_LOOPS", "1"))
INVESTIGATION_RESEARCH_CONCURRENCY = int(
    os.getenv("INVESTIGATION_RESEARCH_CONCURRENCY", "4")
)
INVESTIGATION_TOOL_RETRIES = int(os.getenv("INVESTIGATION_TOOL_RETRIES", "1"))
INVESTIGATION_GRAPH_ENABLED = os.getenv(
    "INVESTIGATION_GRAPH_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
MAX_HYPOTHESES = int(os.getenv("MAX_HYPOTHESES", "6"))

# PRD v3 Phase 12 — proactive monitoring notifications (materiality bar)
INVESTIGATION_NOTIFY_ENABLED = os.getenv(
    "INVESTIGATION_NOTIFY_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
INVESTIGATION_NOTIFY_MIN_SCORE = float(
    os.getenv("INVESTIGATION_NOTIFY_MIN_SCORE", "45")
)
INVESTIGATION_NOTIFY_MIN_Z = float(os.getenv("INVESTIGATION_NOTIFY_MIN_Z", "2.0"))
INVESTIGATION_NOTIFY_MIN_DEPTH = (
    os.getenv("INVESTIGATION_NOTIFY_MIN_DEPTH", "standard") or "standard"
).strip().lower()
INVESTIGATION_NOTIFY_COOLDOWN_HOURS = float(
    os.getenv("INVESTIGATION_NOTIFY_COOLDOWN_HOURS", "6")
)
ALERT_EMAIL_ENABLED = os.getenv("ALERT_EMAIL_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Phase 12 — environment + LLM / request safeguards
ENVIRONMENT = (
    os.getenv("ENVIRONMENT", "").strip()
    or os.getenv("SENTRY_ENVIRONMENT", "").strip()
    or "development"
).lower()
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(1_000_000)))
MAX_LLM_CALLS_PER_INVESTIGATION = int(os.getenv("MAX_LLM_CALLS_PER_INVESTIGATION", "24"))
MAX_TOKENS_PER_INVESTIGATION = int(os.getenv("MAX_TOKENS_PER_INVESTIGATION", "80000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "1"))
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RATE_LIMIT_EXPENSIVE_PER_MINUTE = int(os.getenv("RATE_LIMIT_EXPENSIVE_PER_MINUTE", "8"))
SWEEP_LOCK_TTL_SECONDS = int(os.getenv("SWEEP_LOCK_TTL_SECONDS", "180"))

# Repo Phase 12 — observability (all optional)
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LANGSMITH_API_KEY = (
    os.getenv("LANGSMITH_API_KEY", "").strip()
    or os.getenv("LANGCHAIN_API_KEY", "").strip()
)
LANGSMITH_PROJECT = (
    os.getenv("LANGSMITH_PROJECT", "").strip()
    or os.getenv("LANGCHAIN_PROJECT", "").strip()
    or "quantpilot"
)
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite:")


def get_allowed_origins() -> List[str]:
    raw = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
