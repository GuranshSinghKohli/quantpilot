"""Test wiring for Spark processing: a tmp lake, a migrated tmp SQLite, and helpers
to land raw events exactly as the lake writer does (plus deliberately broken
objects). SYNTHETIC data only; no JVM needed to import this module."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.domain.ingestion_models import IngestionRecord, ProcessingJob
from app.pipeline.contracts import SOURCES
from app.pipeline.lake import ObjectStore
from app.pipeline.lake.keys import raw_key, raw_key_for
from app.pipeline.serde import encode_event
from app.pipeline.spark.plan import InputPlan, ProcessingRequest, plan_inputs
from app.pipeline.tracking import RunTracker
from app.repositories import ingestion as repo

MAX_BYTES = 1_000_000
TODAY = date(2026, 9, 2)


@dataclass
class ProcessingEnv:
    store: ObjectStore
    sessions: sessionmaker

    def land(self, *events, track: bool = True) -> List[str]:
        """Write events to raw like the lake writer and (optionally) track them as LANDED."""
        keys = []
        for event in events:
            key = raw_key_for(event)
            self.store.put_if_absent(key, encode_event(event, MAX_BYTES), content_type="application/json")
            keys.append(key)
        if track:
            self._track(events, keys)
        return keys

    def land_document(self, event, mutate: Callable[[dict], Optional[dict]]) -> str:
        """Land ``event``'s JSON after ``mutate`` edits it, at the event's own raw key."""
        doc = json.loads(encode_event(event, MAX_BYTES))
        doc = mutate(doc) or doc
        return self.land_bytes(raw_key_for(event), json.dumps(doc).encode())

    def land_bytes(self, key: str, data: bytes) -> str:
        self.store.put_if_absent(key, data, content_type="application/json")
        return key

    def raw_key(self, domain: str, day: date, event_id: str) -> str:
        return raw_key(domain, datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc), event_id)

    def plan(self, domain: str, *, start: date = TODAY, end: Optional[date] = None, prefix=None, objects=(),
             output_prefix=None, today: date = TODAY, max_objects: int = 10_000) -> InputPlan:
        request = ProcessingRequest(domain, start=None if (prefix or objects) else start,
                                    end=None if (prefix or objects) else (end or start),
                                    prefix=prefix, objects=tuple(objects), output_prefix=output_prefix)
        return plan_inputs(self.store, request, max_window_days=366, max_objects=max_objects, today=today)

    def snapshot(self, prefix: str = "raw/") -> Dict[str, str]:
        """sha256 of every object under ``prefix`` (raw immutability checks)."""
        return {info.key: hashlib.sha256(self.store.get(info.key)).hexdigest() for info in self.store.list(prefix)}

    def keys(self, prefix: str) -> List[str]:
        return sorted(info.key for info in self.store.list(prefix))

    def json(self, key: str) -> dict:
        return json.loads(self.store.get(key))

    def job(self, job_id) -> ProcessingJob:
        with self.sessions() as db:
            return db.get(ProcessingJob, job_id)

    def jobs(self) -> List[ProcessingJob]:
        with self.sessions() as db:
            return list(db.scalars(select(ProcessingJob).order_by(ProcessingJob.started_at)))

    def records(self) -> Dict[str, IngestionRecord]:
        with self.sessions() as db:
            return {r.event_id: r for r in db.scalars(select(IngestionRecord))}

    def _track(self, events: Iterable, keys: List[str]) -> None:
        by_source: Dict[str, list] = {}
        for event, key in zip(events, keys):
            by_source.setdefault(event.source, []).append((event, key))
        for source, items in by_source.items():
            tracker = RunTracker(self.sessions, SOURCES[source], trigger="test")
            tracker.start()
            with self.sessions() as db:
                for event, key in items:
                    ref = repo.EventRef(event.event_id, event.domain, event.external_id, event.dedup_key,
                                        event.content_hash, event.schema_version)
                    repo.claim_event(db, ref, run_id=tracker.run_id, source_id=tracker.source_id, topic="test.raw")
                    repo.record_landing(db, event.event_id, raw_uri=self.store.uri(key), topic="test.raw",
                                        partition=0, offset=0)
                db.commit()
