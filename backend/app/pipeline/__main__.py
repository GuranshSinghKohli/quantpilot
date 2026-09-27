"""Pipeline CLI: ``python -m app.pipeline <command>`` (run from ``backend/``)."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def _schemas(args: argparse.Namespace) -> int:
    from app.pipeline import schemas

    if args.check:
        stale = schemas.drift()
        if stale:
            print("stale JSON schemas (run `python -m app.pipeline schemas`): " + ", ".join(stale))
            return 1
        print("JSON schemas match the contracts")
        return 0
    for path in schemas.write():
        print(path)
    return 0


def _lake(args: argparse.Namespace) -> int:
    import json

    from app.pipeline.lake import build_object_store, probe_write_once

    store = build_object_store()
    report = {}
    if args.action == "init":
        if store.backend == "s3":
            report["init"] = store.ensure_bucket()
        else:
            store.root.mkdir(parents=True, exist_ok=True)
            report["init"] = {"root": store.root.as_posix(), "created": True}
    report["check"] = store.check()
    report["write_once_probe"] = probe_write_once(store)
    ok = report["write_once_probe"]["ok"]
    if store.backend == "s3" and report["check"]["versioning"] != "Enabled":
        report["problem"] = "bucket versioning is not enabled (run `python -m app.pipeline lake init`)"
        ok = False
    if not report["write_once_probe"]["ok"]:
        report["problem"] = "the store overwrote an existing object; conditional writes are not honoured"
    print(json.dumps(report, indent=2, default=str))
    return 0 if ok else 1


def _require_migrated_db():
    """Tracking writes need the Phase 2 tables. Never migrate from here."""
    from app.core.db import SessionLocal, engine
    from app.core.migrations import migration_state

    state = migration_state(engine)
    if not state.is_current:
        raise SystemExit(
            f"database {engine.url.render_as_string(hide_password=True)} is at revision {state.current}, "
            f"head is {state.head}; run migrations first (make migrate / compose migrate service)"
        )
    return SessionLocal


def _producer(settings, metrics):
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.producer import EventProducer
    from app.pipeline.retry import RetryPolicy

    return EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings), metrics=metrics)


def _kafka(args: argparse.Namespace) -> int:
    import json

    from confluent_kafka.admin import AdminClient

    from app.core.settings import get_settings
    from app.pipeline.kafka.config import admin_config, consumer_config
    from app.pipeline.kafka.topics import ensure_topics, topic_specs, verify_topic

    settings = get_settings()
    admin = AdminClient(admin_config(settings))
    specs = topic_specs(settings)
    if args.action == "init":
        reports = ensure_topics(admin, specs)
    else:
        reports = [{"topic": s.name, "action": "checked", "problems": verify_topic(admin, s)} for s in specs]
    report = {
        "bootstrap": settings.KAFKA_BOOTSTRAP_SERVERS,
        "topics": [dict(r, partitions=s.partitions, replication_factor=s.replication_factor,
                        retention_hours=s.retention_ms // 3_600_000) for r, s in zip(reports, specs)],
    }
    if args.action == "check":
        from confluent_kafka import Consumer, TopicPartition

        from app.pipeline.kafka.consumer import committed_lag

        group = settings.KAFKA_CONSUMER_GROUP_LAKE
        probe = Consumer(consumer_config(settings, group))  # reads committed offsets; never subscribes or commits
        try:
            partitions = [
                TopicPartition(s.name, p)
                for s in specs if s.name != settings.KAFKA_TOPIC_DLQ
                for p in range(s.partitions)
            ]
            lag = committed_lag(probe, partitions)
        finally:
            probe.close()
        report["consumer_group"] = {"group": group, "lag": {f"{t}[{p}]": v for (t, p), v in sorted(lag.items())}}
    print(json.dumps(report, indent=2))
    return 1 if any(r["problems"] for r in reports) else 0


def _lake_writer(args: argparse.Namespace) -> int:
    import signal

    from app.core.settings import get_settings
    from app.pipeline.kafka.consumer import build_consumer
    from app.pipeline.kafka.producer import DeadLetterPublisher
    from app.pipeline.lake import build_object_store
    from app.pipeline.lake_writer import LakeWriter
    from app.pipeline.metrics import Metrics
    from app.pipeline.retry import RetryPolicy

    settings = get_settings()
    sessions = _require_migrated_db()
    metrics = Metrics()
    producer = _producer(settings, metrics)
    consumer = build_consumer(settings, settings.KAFKA_CONSUMER_GROUP_LAKE)
    stopping = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.append(True))
    writer = LakeWriter(
        consumer=consumer, store=build_object_store(settings), session_factory=sessions,
        dlq=DeadLetterPublisher(producer, settings.KAFKA_TOPIC_DLQ), topics=settings.raw_topics,
        max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES, policy=RetryPolicy.from_settings(settings),
        metrics=metrics, poll_timeout=settings.KAFKA_POLL_TIMEOUT_SECONDS,
    )
    try:
        writer.run(max_messages=args.max_messages, idle_timeout_s=args.idle_timeout, should_stop=lambda: bool(stopping))
    finally:
        consumer.close()
        producer.close()
    return 0


def _publish_synthetic(args: argparse.Namespace) -> int:
    import json

    from app.core.settings import get_settings
    from app.pipeline import synthetic
    from app.pipeline.contracts import SOURCES
    from app.pipeline.kafka.producer import DeadLetterPublisher
    from app.pipeline.metrics import Metrics
    from app.pipeline.publisher import EventPublisher
    from app.pipeline.tracking import RunTracker

    settings = get_settings()
    sessions = _require_migrated_db()
    metrics = Metrics()
    producer = _producer(settings, metrics)
    tracker = RunTracker(sessions, SOURCES[synthetic.SOURCE], trigger="cli", params={"count": args.count})
    run_id = tracker.start()
    try:
        events = synthetic.market_events(args.count, run_id=run_id)
        tracker.counters.received = len(events)
        publisher = EventPublisher(
            session_factory=sessions, producer=producer, dlq=DeadLetterPublisher(producer, settings.KAFKA_TOPIC_DLQ),
            topics=settings.raw_topics, max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES,
        )
        summary = publisher.publish(tracker, events)
    except BaseException as exc:
        tracker.finish(fatal_error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        producer.close()
    status = tracker.finish()
    print(json.dumps({
        "run_id": str(run_id), "status": status.value, "source": synthetic.SOURCE, "events": len(events),
        "published": summary.published, "duplicate": summary.duplicate, "publish_failed": summary.publish_failed,
        "dead_lettered": summary.dead_lettered, "metrics": metrics.snapshot(),
    }, indent=2))
    return 0 if summary.publish_failed == 0 and summary.dead_lettered == 0 else 1


def _dlq(args: argparse.Namespace) -> int:
    import json
    import uuid

    from confluent_kafka import OFFSET_BEGINNING, Consumer, TopicPartition

    from app.core.settings import get_settings
    from app.pipeline.kafka.config import consumer_config
    from app.pipeline.serde import decode_dead_letter

    settings = get_settings()
    topic = settings.KAFKA_TOPIC_DLQ
    # Throwaway group id and assign(): reads from the beginning, never commits.
    consumer = Consumer(consumer_config(settings, f"quantpilot.dlq-reader.{uuid.uuid4().hex[:8]}"))
    try:
        partitions = consumer.list_topics(topic, timeout=10).topics[topic].partitions
        consumer.assign([TopicPartition(topic, p, OFFSET_BEGINNING) for p in partitions])
        letters, empty_polls = [], 0
        while len(letters) < args.limit and empty_polls < 3:
            message = consumer.poll(1.0)
            if message is None:
                empty_polls += 1
                continue
            if message.error():
                continue
            letter = decode_dead_letter(message.value())
            item = letter.model_dump(mode="json", exclude=set() if args.show_payload else {"original_value"})
            item["dlq_offset"] = message.offset()
            letters.append(item)
    finally:
        consumer.close()
    print(json.dumps({"topic": topic, "count": len(letters), "dead_letters": letters}, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    schema_cmd = commands.add_parser("schemas", help="export contract JSON Schemas to docs/schemas/")
    schema_cmd.add_argument("--check", action="store_true", help="fail if docs/schemas/ is stale")
    schema_cmd.set_defaults(handler=_schemas)

    lake_cmd = commands.add_parser("lake", help="object store: init (bucket + versioning) or check")
    lake_cmd.add_argument("action", choices=["init", "check"])
    lake_cmd.set_defaults(handler=_lake)

    kafka_cmd = commands.add_parser("kafka", help="topics: init (create + verify) or check (verify + group lag)")
    kafka_cmd.add_argument("action", choices=["init", "check"])
    kafka_cmd.set_defaults(handler=_kafka)

    writer_cmd = commands.add_parser("lake-writer", help="consume raw topics into the lake (group KAFKA_CONSUMER_GROUP_LAKE)")
    writer_cmd.add_argument("--max-messages", type=int, default=None)
    writer_cmd.add_argument("--idle-timeout", type=float, default=None, help="exit after this many idle seconds")
    writer_cmd.set_defaults(handler=_lake_writer)

    synth_cmd = commands.add_parser("publish-synthetic", help="publish deterministic SYNTHETIC market events")
    synth_cmd.add_argument("--count", type=int, default=5)
    synth_cmd.set_defaults(handler=_publish_synthetic)

    dlq_cmd = commands.add_parser("dlq", help="list dead letters (reads without committing)")
    dlq_cmd.add_argument("--limit", type=int, default=50)
    dlq_cmd.add_argument("--show-payload", action="store_true")
    dlq_cmd.set_defaults(handler=_dlq)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
