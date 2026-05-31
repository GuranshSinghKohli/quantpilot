import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "quantpilot.log"

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data") and record.extra_data:
            payload["extra_data"] = record.extra_data
        if record.exc_info and record.exc_info[0]:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        formatter = JsonFormatter()
        root = logging.getLogger("quantpilot")
        root.setLevel(logging.INFO)
        if not root.handlers:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(formatter)
            root.addHandler(console)
            file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        _CONFIGURED = True
    return logging.getLogger(f"quantpilot.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **extra: Any) -> None:
    record = logger.makeRecord(
        logger.name,
        level,
        "(observability)",
        0,
        message,
        (),
        None,
    )
    record.extra_data = extra
    logger.handle(record)
