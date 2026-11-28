"""Read-only graph API: no Cypher, bounded results, health stays cheap."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.session import init_db
from app.main import app

client = TestClient(app)


def test_health_has_no_neo4j_probe():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "neo4j" not in body


def test_ready_reports_neo4j_without_requiring_it():
    body = client.get("/ready").json()
    assert "neo4j" in body
    assert body["neo4j"] in {"ok", "unavailable", "unconfigured"}


def test_missing_entity_is_404():
    init_db()
    res = client.get("/api/entities/00000000-0000-0000-0000-000000000001")
    assert res.status_code == 404


def test_no_arbitrary_cypher_endpoint():
    assert client.get("/api/graph/cypher").status_code == 404
    assert client.post("/api/graph/query", json={"cypher": "MATCH (n) RETURN n"}).status_code == 404


def test_metrics_have_no_graph_identifiers():
    text = client.get("/metrics").text
    for forbidden in ("entity_id=", "relationship_id=", "ticker=", "cik="):
        assert forbidden not in text
