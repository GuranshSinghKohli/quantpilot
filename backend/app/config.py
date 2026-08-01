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

# Phase 11 — Browser MCP / OpenClaw IR retrieval
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

# Phase 12 — observability (all optional)
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
