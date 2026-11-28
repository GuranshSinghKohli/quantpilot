"""Confirmed events project to CanonicalEvent; rejected associations stay off the graph."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import EventStatus
from app.domain.models import Event, EventEntity
from app.graph.projector import project
from app.graph.reconcile import reconcile
from app.graph.store import probe_neo4j, run_cypher
from tests.unit.market.helpers import company, confirmed_event, source, evidence
from app.domain.enums import EventEntityRole, EventType
from app.domain.schemas import EventEntityIn, EventIn
from app.repositories import events

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path, neo4j_settings):
    engine = build_engine(f"sqlite:///{tmp_path / 'neo4j-events.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_confirmed_event_projects_and_is_idempotent(db, neo4j_settings):
    assert probe_neo4j() == "ok"
    nvda = company(db, "Neo NVIDIA", "NVDX", "808")
    event = confirmed_event(db, nvda.entity_id, title="Neo partnership")
    db.commit()
    t0 = time.perf_counter()
    first = project(db)
    project_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    second = project(db)
    project_repeat_ms = (time.perf_counter() - t1) * 1000
    assert first["ok"] and second["ok"]
    assert first["events"] == 1 and second["events"] == 1
    nodes = run_cypher(
        "MATCH (e:CanonicalEvent {event_id: $id}) RETURN count(e) AS n",
        {"id": str(event.id)},
    )[0]["n"]
    rels = run_cypher(
        "MATCH (e:CanonicalEvent {event_id: $id})-[r:INVOLVES]->(n:Entity) RETURN count(r) AS n",
        {"id": str(event.id)},
    )[0]["n"]
    assert nodes == 1 and rels == 1
    t2 = time.perf_counter()
    report = reconcile(db)
    reconcile_ms = (time.perf_counter() - t2) * 1000
    print(
        f"PERF graph_project={project_ms:.2f}ms graph_project_repeat={project_repeat_ms:.2f}ms "
        f"graph_reconcile={reconcile_ms:.2f}ms"
    )
    assert report["missing_events"] == []
    assert report["extra_events"] == []
    assert report["missing_event_relationships"] == []


def test_rejected_event_is_not_projected(db, neo4j_settings):
    nvda = company(db, "Reject Co", "REJ", "809")
    src = source(db, url="https://example.com/rejected-event")
    ev = evidence(db, src.id, "Rejected")
    event, _ = events.upsert_event(
        db,
        EventIn(
            event_type=EventType.NEWS,
            title="Rejected news",
            detected_at=NOW,
            occurred_at=NOW,
            primary_source_id=src.id,
            evidence_id=ev.id,
            entities=[EventEntityIn(entity_id=nvda.entity_id, role=EventEntityRole.SUBJECT)],
        ),
    )
    event.status = EventStatus.REJECTED.value
    db.commit()
    project(db)
    nodes = run_cypher(
        "MATCH (e:CanonicalEvent {event_id: $id}) RETURN count(e) AS n",
        {"id": str(event.id)},
    )[0]["n"]
    assert nodes == 0
    assert db.get(Event, event.id) is not None
    assert db.scalar(select(EventEntity).where(EventEntity.event_id == event.id)) is not None


def test_event_projection_failure_does_not_change_postgres(db, neo4j_settings, monkeypatch):
    nvda = company(db, "Stay Co", "STAY", "810")
    event = confirmed_event(db, nvda.entity_id, title="Stay")
    db.commit()
    from app.graph import projector

    monkeypatch.setattr(projector, "upsert_event_node", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    result = project(db, init_schema=False)
    assert result["failures"] >= 1
    assert db.get(Event, event.id).title == "Stay"
    assert db.get(type(nvda), nvda.entity_id).legal_name == "Stay Co"
