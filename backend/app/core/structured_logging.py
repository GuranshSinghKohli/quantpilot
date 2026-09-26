"""JSON logs to stdout, joinable across API and future workers.

Every record carries service identity (service, env, version) plus request /
investigation correlation IDs and redacts secret-looking fields.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

from app.core.settings import BACKEND_DIR, get_settings
from app.security.request_context import correlation_fields
from app.security.secrets import redact_mapping

LOG_DIR = BACKEND_DIR / "logs"
LOG_FILE = LOG_DIR / "quantpilot.log"

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, env: str, version: str) -> None:
        super().__init__()
        self._identity = {"service": service, "env": env, "version": version}

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            **self._identity,
            "module": record.name,
            "message": record.getMessage(),
        }
        if getattr(record, "extra_data", None):
            payload["extra_data"] = record.extra_data
        if record.exc_info and record.exc_info[0]:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    settings = get_settings()
    formatter = JsonFormatter(
        settings.SERVICE_NAME, settings.ENVIRONMENT, settings.SERVICE_VERSION
    )
    root = logging.getLogger("quantpilot")
    root.setLevel(logging.INFO)
    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)
        # Containers ship stdout; a local rotating file is a development convenience.
        if settings.ENVIRONMENT == "development":
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"quantpilot.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **extra: Any) -> None:
    merged = {**correlation_fields(), **extra}
    record = logger.makeRecord(logger.name, level, "(observability)", 0, message, (), None)
    record.extra_data = redact_mapping(merged)
    logger.handle(record)
