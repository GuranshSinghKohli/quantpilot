"""Typed, validated runtime configuration (pydantic-settings).

Read once per process via ``get_settings()``. ``app.config`` re-exports these
values as module attributes for code written before this module existed.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
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

# Modules outside app.config still read os.environ directly (OPENAI_API_KEY,
# SEC_EDGAR_USER_AGENT, ...), so .env must populate the process environment.
_ENV_FILE = BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
else:
    load_dotenv()

_DEFAULT_SQLITE = f"sqlite:///{(BACKEND_DIR / 'data' / 'quantpilot.db').as_posix()}"
_DEV_JWT_SECRET = "dev-only-change-me-quantpilot"
_KAFKA_NAME = re.compile(r"[A-Za-z0-9._-]{1,249}")
_S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    # Service identity (logs, traces, /health)
    SERVICE_NAME: str = "quantpilot-api"
    SERVICE_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"

    # Data stores
    DATABASE_URL: str = _DEFAULT_SQLITE
    REDIS_URL: str = ""
    # Unset: migrate on startup only in development. Deploys run `alembic upgrade head`.
    DB_AUTO_MIGRATE: Optional[bool] = None

    # Auth
    JWT_SECRET_KEY: SecretStr = SecretStr(_DEV_JWT_SECRET)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_DAYS: int = Field(7, ge=1)

    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    QUOTE_CACHE_TTL_SECONDS: int = Field(60, ge=0)

    # Browser MCP / OpenClaw document retrieval
    BROWSER_MCP_ENABLED: bool = True
    OPENCLAW_BROWSER_URL: str = ""
    OPENCLAW_GATEWAY_TOKEN: SecretStr = SecretStr("")
    OPENCLAW_BROWSER_PROFILE: str = "openclaw"
    MCP_BROWSER_TIMEOUT_SECONDS: int = Field(45, ge=1)
    MAX_BROWSER_TASKS_PER_INVESTIGATION: int = Field(2, ge=0)
    MAX_BROWSER_PAGES_PER_TASK: int = Field(4, ge=0)
    MAX_BROWSER_RETRIES: int = Field(1, ge=0)
    BROWSER_NAV_TIMEOUT_SECONDS: int = Field(20, ge=1)
    BROWSER_DISCOVERY_TIMEOUT_SECONDS: int = Field(30, ge=1)
    BROWSER_EXTRACT_TIMEOUT_SECONDS: int = Field(15, ge=1)

    # Investigation sweep + trigger thresholds
    INVESTIGATIONS_SWEEP_ENABLED: bool = False
    INVESTIGATION_SWEEP_INTERVAL_MINUTES: int = Field(30, ge=1)
    INVESTIGATION_SWEEP_MAX_PER_RUN: int = Field(5, ge=0)
    INVESTIGATION_COOLDOWN_HOURS: float = Field(6.0, ge=0)
    INVESTIGATION_VOL_Z_THRESHOLD: float = 1.5
    INVESTIGATION_IDIO_PCT: float = 1.0
    INVESTIGATION_BENCHMARK: str = "SPY"
    INVESTIGATION_PRICE_MOVE_THRESHOLD: float = 2.5
    INVESTIGATION_MATERIALITY_THRESHOLD: float = 40.0
    INVESTIGATION_EXPOSURE_WEIGHT: float = 100.0
    INVESTIGATION_EARNINGS_LOOKAHEAD_DAYS: int = Field(2, ge=0)
    INVESTIGATION_EARNINGS_LOOKBACK_DAYS: int = Field(1, ge=0)
    INVESTIGATION_FILING_LOOKBACK_DAYS: int = Field(3, ge=0)
    INVESTIGATION_EVENT_BUCKET_HOURS: int = Field(6, ge=1)
    INVESTIGATION_REOPEN_MOVE_DELTA: float = 3.0
    MAX_INVESTIGATION_ITERATIONS: int = Field(2, ge=0)
    MAX_CHALLENGE_LOOPS: int = Field(1, ge=0)
    INVESTIGATION_RESEARCH_CONCURRENCY: int = Field(4, ge=1)
    INVESTIGATION_TOOL_RETRIES: int = Field(1, ge=0)
    INVESTIGATION_GRAPH_ENABLED: bool = True
    MAX_HYPOTHESES: int = Field(6, ge=1)

    # Material-investigation notifications
    INVESTIGATION_NOTIFY_ENABLED: bool = True
    INVESTIGATION_NOTIFY_MIN_SCORE: float = 45.0
    INVESTIGATION_NOTIFY_MIN_Z: float = 2.0
    INVESTIGATION_NOTIFY_MIN_DEPTH: Literal["light", "standard", "deep"] = "standard"
    INVESTIGATION_NOTIFY_COOLDOWN_HOURS: float = Field(6.0, ge=0)
    ALERT_EMAIL_ENABLED: bool = False

    # Request / LLM safeguards
    MAX_REQUEST_BODY_BYTES: int = Field(1_000_000, ge=1)
    MAX_LLM_CALLS_PER_INVESTIGATION: int = Field(24, ge=1)
    MAX_TOKENS_PER_INVESTIGATION: int = Field(80_000, ge=1)
    MAX_RETRIES: int = Field(1, ge=0)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_EXPENSIVE_PER_MINUTE: int = Field(8, ge=1)
    SWEEP_LOCK_TTL_SECONDS: int = Field(180, ge=1)

    # Ingestion pipeline: Kafka (event backbone)
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TOPIC_NEWS_RAW: str = "quantpilot.news.raw"
    KAFKA_TOPIC_MARKET_RAW: str = "quantpilot.market.raw"
    KAFKA_TOPIC_COMPANY_RAW: str = "quantpilot.company.raw"
    KAFKA_TOPIC_DLQ: str = "quantpilot.ingestion.dlq"
    KAFKA_CONSUMER_GROUP_LAKE: str = "quantpilot.lake-writer"
    KAFKA_MAX_MESSAGE_BYTES: int = Field(1_000_000, ge=1024)

    # Ingestion pipeline: data lake (S3 API; MinIO locally)
    LAKE_BACKEND: Literal["s3", "local"] = "s3"
    LAKE_BUCKET: str = "quantpilot-lake"
    LAKE_ENDPOINT_URL: str = ""
    LAKE_REGION: str = "us-east-1"
    LAKE_LOCAL_ROOT: str = (BACKEND_DIR / "data" / "lake").as_posix()

    # Ingestion pipeline: sources + retries
    INGEST_SYMBOLS: str = "AAPL,MSFT,NVDA"
    PIPELINE_RETRY_MAX_ATTEMPTS: int = Field(5, ge=1, le=20)
    PIPELINE_RETRY_BASE_SECONDS: float = Field(0.5, gt=0)
    PIPELINE_RETRY_MAX_SECONDS: float = Field(30.0, gt=0)

    # Observability (all optional)
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: SecretStr = SecretStr("")
    LANGSMITH_PROJECT: str = "quantpilot"
    SENTRY_DSN: str = ""
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_ENABLED: bool = False

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "BROWSER_MCP_ENABLED",
        "DB_AUTO_MIGRATE",
        "INVESTIGATIONS_SWEEP_ENABLED",
        "INVESTIGATION_GRAPH_ENABLED",
        "INVESTIGATION_NOTIFY_ENABLED",
        "ALERT_EMAIL_ENABLED",
        "RATE_LIMIT_ENABLED",
        "LANGSMITH_TRACING",
        "OTEL_ENABLED",
        mode="before",
    )
    @classmethod
    def _parse_flag(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return value

    @field_validator("DATABASE_URL", mode="after")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        if not value:
            return _DEFAULT_SQLITE
        # Hosted Postgres often provides postgres://; SQLAlchemy 2 needs postgresql://.
        if value.startswith("postgres://"):
            return "postgresql://" + value[len("postgres://") :]
        return value

    @field_validator("ENVIRONMENT", "INVESTIGATION_NOTIFY_MIN_DEPTH", mode="after")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()

    @field_validator("INVESTIGATION_BENCHMARK", mode="after")
    @classmethod
    def _benchmark_default(cls, value: str) -> str:
        return value or "SPY"

    @field_validator(
        "KAFKA_TOPIC_NEWS_RAW",
        "KAFKA_TOPIC_MARKET_RAW",
        "KAFKA_TOPIC_COMPANY_RAW",
        "KAFKA_TOPIC_DLQ",
        "KAFKA_CONSUMER_GROUP_LAKE",
        mode="after",
    )
    @classmethod
    def _kafka_name(cls, value: str) -> str:
        if not _KAFKA_NAME.fullmatch(value):
            raise ValueError(f"invalid Kafka topic/group name: {value!r}")
        return value

    @field_validator("LAKE_BUCKET")
    @classmethod
    def _bucket_name(cls, value: str) -> str:
        if not _S3_BUCKET.fullmatch(value) or ".." in value:
            raise ValueError(f"invalid S3 bucket name: {value!r}")
        return value

    @field_validator("LAKE_ENDPOINT_URL")
    @classmethod
    def _endpoint_url(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("LAKE_ENDPOINT_URL must be empty (AWS) or an http(s) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _retry_bounds(self) -> "Settings":
        if self.PIPELINE_RETRY_MAX_SECONDS < self.PIPELINE_RETRY_BASE_SECONDS:
            raise ValueError("PIPELINE_RETRY_MAX_SECONDS must be >= PIPELINE_RETRY_BASE_SECONDS")
        topics = [
            self.KAFKA_TOPIC_NEWS_RAW,
            self.KAFKA_TOPIC_MARKET_RAW,
            self.KAFKA_TOPIC_COMPANY_RAW,
            self.KAFKA_TOPIC_DLQ,
        ]
        if len(set(topics)) != len(topics):
            raise ValueError("Kafka raw and DLQ topics must be distinct")
        return self

    @property
    def raw_topics(self) -> Dict[str, str]:
        """Raw topic per pipeline domain (news | market | company)."""
        return {
            "news": self.KAFKA_TOPIC_NEWS_RAW,
            "market": self.KAFKA_TOPIC_MARKET_RAW,
            "company": self.KAFKA_TOPIC_COMPANY_RAW,
        }

    @property
    def ingest_symbols(self) -> List[str]:
        return [s.strip().upper() for s in self.INGEST_SYMBOLS.split(",") if s.strip()]

    @property
    def auto_migrate(self) -> bool:
        if self.DB_AUTO_MIGRATE is None:
            return self.ENVIRONMENT == "development"
        return self.DB_AUTO_MIGRATE

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite:")

    @property
    def allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    def validate_for_environment(self) -> None:
        """Refuse to boot a production process with development-only secrets."""
        if self.ENVIRONMENT == "production":
            if self.JWT_SECRET_KEY.get_secret_value() in {"", _DEV_JWT_SECRET}:
                raise ValueError("JWT_SECRET_KEY must be set in production")


def _env_aliases() -> dict:
    """Legacy env names that map onto a canonical setting."""
    overrides: dict = {}
    if not os.getenv("ENVIRONMENT") and os.getenv("SENTRY_ENVIRONMENT"):
        overrides["ENVIRONMENT"] = os.environ["SENTRY_ENVIRONMENT"]
    if not os.getenv("LANGSMITH_API_KEY") and os.getenv("LANGCHAIN_API_KEY"):
        overrides["LANGSMITH_API_KEY"] = os.environ["LANGCHAIN_API_KEY"]
    if not os.getenv("LANGSMITH_PROJECT") and os.getenv("LANGCHAIN_PROJECT"):
        overrides["LANGSMITH_PROJECT"] = os.environ["LANGCHAIN_PROJECT"]
    return overrides


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(**_env_aliases())
