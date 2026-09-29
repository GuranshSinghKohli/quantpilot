"""Small disposable-infrastructure check for the load harness.

The 100-request matrix is a CLI run, not part of pytest. This test submits
four fixture requests through the existing worker when both disposable
Postgres and the Kafka broker are configured.
"""

from __future__ import annotations

import os
import socket

import pytest

from app.research.loadtest import developer_integrity, run_load


def _host_qp_pg_test_available() -> bool:
    """The smoke test dials qp-pg-test on the host. Spark's broker is not that host."""
    kafka = os.getenv("TEST_KAFKA_BOOTSTRAP", "").strip()
    host = kafka.split(",")[0].rsplit(":", 1)[0] if kafka else ""
    if host not in {"localhost", "127.0.0.1"}:
        return False
    try:
        with socket.create_connection(("127.0.0.1", 55432), timeout=0.5):
            return True
    except OSError:
        return False


def test_load_harness_four_requests():
    if not _host_qp_pg_test_available():
        pytest.skip("host qp-pg-test and localhost Kafka are required")
    before = developer_integrity()
    report = run_load(
        database_url="postgresql://quantpilot:quantpilot@localhost:55432/quantpilot_load",
        kafka_bootstrap=kafka,
        requests=4,
        concurrency=[2],
        scaling_workers=[],
        include_scenarios=False,
        include_matrix=True,
    )
    after = developer_integrity()
    level = report["matrix"][0]
    assert level["requests"] == 4
    assert level["successful_submissions"] == 4
    assert level["incomplete_ids"] == 0
    assert level["duplicate_jobs"] == 0
    assert level["duplicate_claim_statements"] == 0
    assert level["duplicate_reports"] == 0
    assert level["llm"] == "FakeLLMProvider"
    assert report["cardinality"]["forbidden_label_keys"] == []
    assert before["sqlite_sha256"] == after["sqlite_sha256"]
    assert before["lake_file_count"] == after["lake_file_count"]
    assert report["load_database_dropped"] is True
