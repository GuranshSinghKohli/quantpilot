from __future__ import annotations

import json
import logging

from app.core.structured_logging import JsonFormatter
from app.security.request_context import set_request_id
from app.core.structured_logging import log_event


def test_records_carry_service_identity_and_extra():
    formatter = JsonFormatter("quantpilot-api", "test", "9.9.9")
    record = logging.LogRecord("quantpilot.x", logging.INFO, __file__, 1, "hello", (), None)
    record.extra_data = {"job_id": "j-1"}
    payload = json.loads(formatter.format(record))
    assert payload["service"] == "quantpilot-api"
    assert payload["env"] == "test"
    assert payload["version"] == "9.9.9"
    assert payload["message"] == "hello"
    assert payload["extra_data"] == {"job_id": "j-1"}


def test_log_event_adds_correlation_and_redacts_secrets():
    captured = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    logger = logging.getLogger("quantpilot.test_structured_logging")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        set_request_id("req-123")
        log_event(logger, logging.INFO, "event", api_key="sk-live-should-not-appear")
    finally:
        set_request_id("")
        logger.removeHandler(handler)
    extra = captured[0].extra_data
    assert extra["request_id"] == "req-123"
    assert "sk-live-should-not-appear" not in json.dumps(extra)
