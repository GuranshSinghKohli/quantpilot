"""/health stays cheap; /ready reports pipeline; /metrics is Prometheus text."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.observability.http_metrics import HTTP_METRICS, route_family


client = TestClient(app)


def test_health_is_ok_without_kafka_or_lake(monkeypatch):
    called = []

    def boom(*_a, **_k):
        called.append(True)
        raise RuntimeError("should not be called from /health")

    monkeypatch.setattr("app.pipeline.ops.probes.probe_kafka", boom)
    monkeypatch.setattr("app.pipeline.ops.probes.probe_lake", boom)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert called == []


def test_ready_reports_pipeline_without_requiring_it():
    res = client.get("/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["database"] in {"ok", "error"}
    assert "kafka" in body and "lake" in body
    assert body["kafka"] in {"ok", "unavailable", "unconfigured"}
    assert body["lake"] in {"ok", "unavailable"}
    # A down Kafka broker must not by itself make readiness fail.
    if body["database"] == "ok" and body["migrations_current"] and body["redis"] in {"ok", "memory"}:
        assert body["status"] == "ok"


def test_metrics_is_prometheus_and_low_cardinality():
    client.get("/health")
    res = client.get("/metrics")
    assert res.status_code == 200
    text = res.text
    assert "quantpilot_http_requests_total" in text
    for forbidden in ("event_id=", "job_id=", "ticker=", "cik=", "request_id="):
        assert forbidden not in text
    assert route_family("/api/stocks/TSLA") == "/api/stocks"
    assert route_family("/health") == "/health"
    assert set(HTTP_METRICS.label_keys()) <= {
        "domain", "source", "operation", "status", "component", "topic", "partition",
        "result", "kind", "provider", "reason", "method", "route",
    }


def test_metrics_survives_gauge_failure(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.pipeline.ops.export.pipeline_gauges", boom)
    res = client.get("/metrics")
    assert res.status_code == 200
