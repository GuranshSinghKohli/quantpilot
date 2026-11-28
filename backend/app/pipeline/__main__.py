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


def _request(args: argparse.Namespace, settings):
    from app.pipeline.sources.base import IngestRequest
    from app.pipeline.sources.registry import ConfigurationError
    from app.pipeline.sources.window import resolve_window

    defaults = {"news": settings.NEWS_TICKERS, "market": settings.MARKET_SYMBOLS, "company": settings.COMPANY_SYMBOLS}
    if args.all and args.domain != "company":
        raise ConfigurationError("--all applies to company reference data only")
    raw = "" if args.all else (args.symbols if args.symbols is not None else defaults[args.domain])
    symbols = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    if args.domain == "market" and not symbols:
        raise ConfigurationError("market ingestion needs symbols (--symbols or MARKET_SYMBOLS)")
    for flag, value, ceiling in (("--max-records", args.max_records, 50_000), ("--page-size", args.page_size, 1000),
                                 ("--max-pages", args.max_pages, 500)):
        if value is not None and not 1 <= value <= ceiling:
            raise ConfigurationError(f"{flag} must be between 1 and {ceiling}")
    start, end = resolve_window(args.start, args.end, lookback_days=settings.INGEST_LOOKBACK_DAYS,
                                max_window_days=settings.INGEST_MAX_WINDOW_DAYS)
    return IngestRequest(
        start=start, end=end, symbols=symbols,
        max_records=args.max_records or settings.INGEST_MAX_RECORDS,
        page_size=args.page_size or settings.INGEST_PAGE_SIZE,
        max_pages=args.max_pages or settings.INGEST_MAX_PAGES,
    )


def _ingest(args: argparse.Namespace) -> int:
    import json

    from app.core.settings import get_settings
    from app.domain.enums import IngestionRunStatus
    from app.pipeline.ingest import IngestionRunner
    from app.pipeline.kafka.producer import DeadLetterPublisher
    from app.pipeline.metrics import Metrics
    from app.pipeline.publisher import EventPublisher
    from app.pipeline.sources.registry import ConfigurationError, Wiring, build_adapter

    settings = get_settings()
    try:
        request = _request(args, settings)
        metrics = Metrics()
        adapter = build_adapter(args.domain, settings, Wiring(metrics=metrics))
    except (ConfigurationError, ValueError) as exc:
        print(f"ingest {args.domain}: {exc}", file=sys.stderr)
        return 2
    sessions = _require_migrated_db()
    producer = _producer(settings, metrics)
    dlq = DeadLetterPublisher(producer, settings.KAFKA_TOPIC_DLQ)
    publisher = EventPublisher(session_factory=sessions, producer=producer, dlq=dlq, topics=settings.raw_topics,
                               max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES)
    runner = IngestionRunner(adapter=adapter, publisher=publisher, dlq=dlq, session_factory=sessions,
                             max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES, metrics=metrics, trigger="cli")
    try:
        result = runner.run(request)
    finally:
        adapter.close()
        producer.close()
    print(json.dumps({**result.as_dict(), "metrics": metrics.snapshot()}, indent=2, default=str))
    return 0 if result.status == IngestionRunStatus.COMPLETED else 1


def _sources(args: argparse.Namespace) -> int:
    import json

    from app.core.settings import get_settings
    from app.pipeline.sources.registry import DOMAINS, check_health

    settings = get_settings()
    unknown = [d for d in args.domains if d not in DOMAINS]
    if unknown:
        print(f"unknown source domains {unknown}; expected {list(DOMAINS)}", file=sys.stderr)
        return 2
    reports = [check_health(domain, settings).as_dict() for domain in (args.domains or DOMAINS)]
    print(json.dumps({"sources": reports}, indent=2, default=str))
    # Unconfigured sources are reported, not failed; configured-but-broken ones fail.
    return 1 if any(r["configured"] and not r["healthy"] for r in reports) else 0


def _process(args: argparse.Namespace) -> int:
    import json
    from datetime import date

    from app.core.settings import get_settings
    from app.domain.enums import ProcessingJobStatus
    from app.pipeline.lake import build_object_store
    from app.pipeline.metrics import Metrics
    from app.pipeline.spark.config import ProcessingConfigError, SparkConfig
    from app.pipeline.spark.jobs.engine import SparkEngine
    from app.pipeline.spark.jobs.runner import ProcessingJobRunner
    from app.pipeline.spark.plan import DOMAINS, ProcessingRequest, plan_inputs

    settings = get_settings()
    domains = list(DOMAINS) if args.domain == "all" else [args.domain]
    try:
        if len(domains) > 1 and (args.prefix or args.objects or args.objects_file or args.output_prefix):
            raise ProcessingConfigError("--prefix, --objects and --output-prefix apply to a single domain")
        objects = [k.strip() for k in (args.objects or "").split(",") if k.strip()]
        if args.objects_file:
            with open(args.objects_file, encoding="utf-8") as handle:
                objects += [line.strip() for line in handle if line.strip() and not line.startswith("#")]
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        store = build_object_store(settings)
        plans = [
            plan_inputs(store, ProcessingRequest(domain, start=start, end=end, prefix=args.prefix,
                                                 objects=tuple(objects), output_prefix=args.output_prefix),
                        max_window_days=settings.PROCESS_MAX_WINDOW_DAYS, max_objects=settings.PROCESS_MAX_INPUT_OBJECTS)
            for domain in domains
        ]
        engine = SparkEngine(store, SparkConfig.from_settings(settings))
    except (ProcessingConfigError, ValueError, OSError) as exc:
        print(f"process {args.domain}: {exc}", file=sys.stderr)
        return 2
    sessions = _require_migrated_db()
    metrics = Metrics()
    runner = ProcessingJobRunner(store=store, session_factory=sessions, engine=engine, metrics=metrics,
                                 code_version=settings.CODE_VERSION)
    results, ok = [], True
    try:
        for plan in plans:
            try:
                result = runner.run(plan)
                results.append(result.as_dict())
                ok = ok and result.status == ProcessingJobStatus.COMPLETED
            except ProcessingConfigError as exc:
                results.append({"domain": plan.domain, "status": "FAILED", "error": str(exc)})
                ok = False
                break
            except Exception as exc:  # the job row is already FAILED; report and continue with the next domain
                results.append({"domain": plan.domain, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:2000]})
                ok = False
    finally:
        engine.close()
    print(json.dumps({"jobs": results, "metrics": metrics.snapshot()}, indent=2, default=str))
    return 0 if ok else 1


def _load(args: argparse.Namespace) -> int:
    import json
    import uuid
    from datetime import date

    from app.core.settings import get_settings
    from app.domain.enums import CanonicalLoadStatus
    from app.pipeline.lake import build_object_store
    from app.pipeline.load import CanonicalLoadRunner, LoadConfigError, select_jobs
    from app.pipeline.load.source import check_selection
    from app.pipeline.load.validation import synthetic_allowed
    from app.pipeline.spark.config import ProcessingConfigError

    try:
        job_id = uuid.UUID(args.job) if args.job else None
        check_selection(job_id=job_id, manifest=args.manifest, domain=args.domain, latest=args.latest)
        since = date.fromisoformat(args.since) if args.since else None
        until = date.fromisoformat(args.until) if args.until else None
        if since and until and since > until:
            raise LoadConfigError("--since is after --until")
        if args.batch_size < 1:
            raise LoadConfigError("--batch-size must be at least 1")
        store = build_object_store(get_settings())
        sessions = _require_migrated_db()
        with sessions() as db:
            if args.allow_synthetic and not synthetic_allowed(db):
                raise LoadConfigError("--allow-synthetic is only accepted on a test database (name contains 'test')")
            jobs = [j.id for j in select_jobs(db, job_id=job_id, manifest=args.manifest, domain=args.domain,
                                              latest=args.latest)]
        runner = CanonicalLoadRunner(store=store, session_factory=sessions, batch_size=args.batch_size,
                                     allow_synthetic=args.allow_synthetic)
    except (LoadConfigError, ValueError, OSError) as exc:
        print(f"load: {exc}", file=sys.stderr)
        return 2
    results, ok = [], True
    for job in jobs:
        try:
            result = runner.run(job, since=since, until=until)
            results.append(result.as_dict())
            ok = ok and result.status is CanonicalLoadStatus.COMPLETE
        except (LoadConfigError, ProcessingConfigError) as exc:  # e.g. another job holds the domain lock
            results.append({"job_id": str(job), "status": "NOT_STARTED", "error": str(exc)})
            ok = False
        except Exception as exc:  # the attempt is already recorded FAILED; report and continue
            results.append({"job_id": str(job), "status": CanonicalLoadStatus.FAILED.value,
                            "error": f"{type(exc).__name__}: {exc}"[:2000]})
            ok = False
    print(json.dumps({"loads": results}, indent=2, default=str))
    return 0 if ok else 1


def _dlq(args: argparse.Namespace) -> int:
    import json

    from app.pipeline.ops.dlq import dlq_summary, inspect_dlq

    if args.summary:
        print(json.dumps(dlq_summary(limit=args.limit), indent=2, default=str))
        return 0
    report = inspect_dlq(limit=args.limit, show_payload=args.show_payload)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("available", True) else 1


def _status(args: argparse.Namespace) -> int:
    import json

    from app.pipeline.ops.status import pipeline_status

    sessions = _require_migrated_db()
    with sessions() as db:
        report = pipeline_status(db, include_kafka=not args.skip_kafka, include_dlq=not args.skip_dlq)
    print(json.dumps(report, indent=2, default=str))
    return 0


def _check(args: argparse.Namespace) -> int:
    import json

    from app.pipeline.ops.check import stale_report

    sessions = _require_migrated_db()
    with sessions() as db:
        report = stale_report(db)
    print(json.dumps(report, indent=2, default=str))
    return 1 if report["stale"] else 0


def _reconcile(args: argparse.Namespace) -> int:
    import json
    import uuid
    from datetime import date

    from app.pipeline.lake import build_object_store
    from app.pipeline.ops.reconcile import reconcile

    sessions = _require_migrated_db()
    job_id = uuid.UUID(args.job) if args.job else None
    since = date.fromisoformat(args.since) if args.since else None
    until = date.fromisoformat(args.until) if args.until else None
    store = build_object_store()
    with sessions() as db:
        report = reconcile(db, store, domain=args.domain, job_id=job_id, since=since, until=until)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


def _quality(args: argparse.Namespace) -> int:
    import json

    from app.pipeline.ops.quality import quality_summary

    sessions = _require_migrated_db()
    with sessions() as db:
        report = quality_summary(db, domain=args.domain)
    print(json.dumps(report, indent=2, default=str))
    return 0


def _retry_load(args: argparse.Namespace) -> int:
    import json
    import uuid

    from app.pipeline.lake import build_object_store
    from app.pipeline.load.errors import LoadConfigError
    from app.pipeline.ops.recover import retry_load

    sessions = _require_migrated_db()
    try:
        result = retry_load(build_object_store(), sessions, uuid.UUID(args.job_id))
    except (LoadConfigError, ValueError) as exc:
        print(f"retry-load: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_dict(), indent=2, default=str))
    return 0 if result.status.value == "CANONICAL_LOAD_COMPLETE" else 1


def _reprocess_job(args: argparse.Namespace) -> int:
    import json
    import uuid

    from app.pipeline.lake import build_object_store
    from app.pipeline.load.errors import LoadConfigError
    from app.pipeline.ops.recover import reprocess_job

    sessions = _require_migrated_db()
    try:
        result = reprocess_job(build_object_store(), sessions, uuid.UUID(args.job_id))
    except (LoadConfigError, ValueError) as exc:
        print(f"reprocess-job: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_dict(), indent=2, default=str))
    return 0 if result.status.value == "COMPLETED" else 1


def _retry_ingestion(args: argparse.Namespace) -> int:
    import json
    import uuid

    from app.pipeline.load.errors import LoadConfigError
    from app.pipeline.ops.recover import retry_ingestion
    from app.pipeline.sources.registry import ConfigurationError

    sessions = _require_migrated_db()
    try:
        result = retry_ingestion(sessions, uuid.UUID(args.run_id))
    except (LoadConfigError, ConfigurationError, ValueError) as exc:
        print(f"retry-ingestion: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") == "COMPLETED" else 1


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

    ingest_cmd = commands.add_parser("ingest", help="fetch a bounded window from an external source and publish it")
    ingest_cmd.add_argument("domain", choices=["news", "market", "company"])
    ingest_cmd.add_argument("--symbols", default=None,
                            help="comma-separated tickers (default NEWS_TICKERS / MARKET_SYMBOLS / COMPANY_SYMBOLS)")
    ingest_cmd.add_argument("--all", action="store_true", help="company only: every CIK (still capped by --max-records)")
    ingest_cmd.add_argument("--start", default=None, help="YYYY-MM-DD or ISO 8601 with offset (default end - INGEST_LOOKBACK_DAYS)")
    ingest_cmd.add_argument("--end", default=None, help="YYYY-MM-DD (end of that UTC day) or ISO 8601 (default now)")
    ingest_cmd.add_argument("--max-records", type=int, default=None, help="cap across all streams (default INGEST_MAX_RECORDS)")
    ingest_cmd.add_argument("--page-size", type=int, default=None)
    ingest_cmd.add_argument("--max-pages", type=int, default=None, help="per stream")
    ingest_cmd.set_defaults(handler=_ingest)

    sources_cmd = commands.add_parser("sources", help="external source health (separate from the API /health)")
    sources_cmd.add_argument("action", choices=["check"])
    sources_cmd.add_argument("domains", nargs="*", help="news | market | company (default: all)")
    sources_cmd.set_defaults(handler=_sources)

    process_cmd = commands.add_parser("process", help="Spark: raw lake objects → processed Parquet (+ rejects, manifest, processing_jobs)")
    process_cmd.add_argument("domain", choices=["news", "market", "company", "all"])
    process_cmd.add_argument("--start", default=None, help="first raw ingestion date YYYY-MM-DD (default: --end or today UTC)")
    process_cmd.add_argument("--end", default=None, help="last raw ingestion date YYYY-MM-DD (default: today UTC)")
    process_cmd.add_argument("--prefix", default=None, help="raw key prefix instead of a window, e.g. raw/news/year=2026/month=09/")
    process_cmd.add_argument("--objects", default=None, help="comma-separated raw object keys instead of a window")
    process_cmd.add_argument("--objects-file", default=None, help="file with one raw object key per line")
    process_cmd.add_argument("--output-prefix", default=None, help="processed/... folder (default processed/<domain>/)")
    process_cmd.set_defaults(handler=_process)

    load_cmd = commands.add_parser(
        "load", help="processed Parquet of one processing job (via its manifest) → canonical Postgres tables")
    load_cmd.add_argument("--domain", choices=["news", "market", "company", "all"], default=None,
                          help="with --latest: which domain ('all' = the latest job of each)")
    load_cmd.add_argument("--job", default=None, help="processing job id to load")
    load_cmd.add_argument("--manifest", default=None, help="processing manifest key or URI (…/_manifests/processing/<job>.json)")
    load_cmd.add_argument("--latest", action="store_true", help="the most recent COMPLETED processing job of --domain")
    load_cmd.add_argument("--since", default=None, help="only manifest partitions on/after YYYY-MM-DD")
    load_cmd.add_argument("--until", default=None, help="only manifest partitions on/before YYYY-MM-DD")
    load_cmd.add_argument("--batch-size", type=int, default=1000, help="rows per transaction (default 1000)")
    load_cmd.add_argument("--allow-synthetic", action="store_true",
                          help="load SYNTHETIC sources (refused unless the database name contains 'test')")
    load_cmd.set_defaults(handler=_load)

    dlq_cmd = commands.add_parser("dlq", help="list dead letters (reads without committing; never replays)")
    dlq_cmd.add_argument("--limit", type=int, default=50)
    dlq_cmd.add_argument("--show-payload", action="store_true")
    dlq_cmd.add_argument("--summary", action="store_true", help="counts by component/reason; no payloads")
    dlq_cmd.set_defaults(handler=_dlq)

    status_cmd = commands.add_parser("status", help="latest runs/jobs/loads, failures, lag, DLQ summary")
    status_cmd.add_argument("--skip-kafka", action="store_true")
    status_cmd.add_argument("--skip-dlq", action="store_true")
    status_cmd.set_defaults(handler=_status)

    check_cmd = commands.add_parser("check", help="report stale RUNNING jobs (does not mark them failed)")
    check_cmd.set_defaults(handler=_check)

    recon_cmd = commands.add_parser("reconcile", help="scoped lineage/count/state invariants")
    recon_cmd.add_argument("--domain", choices=["news", "market", "company"], default=None)
    recon_cmd.add_argument("--job", default=None, help="processing job id")
    recon_cmd.add_argument("--since", default=None)
    recon_cmd.add_argument("--until", default=None)
    recon_cmd.set_defaults(handler=_reconcile)

    quality_cmd = commands.add_parser("quality", help="per-domain count chain (ingested → canonical)")
    quality_cmd.add_argument("--domain", choices=["news", "market", "company"], default=None)
    quality_cmd.set_defaults(handler=_quality)

    retry_load_cmd = commands.add_parser("retry-load", help="load a COMPLETED job after verifying its manifest")
    retry_load_cmd.add_argument("job_id")
    retry_load_cmd.set_defaults(handler=_retry_load)

    reprocess_cmd = commands.add_parser("reprocess-job", help="re-process the raw keys listed on a job's manifest")
    reprocess_cmd.add_argument("job_id")
    reprocess_cmd.set_defaults(handler=_reprocess_job)

    retry_ing_cmd = commands.add_parser("retry-ingestion", help="new ingest from a FAILED/PARTIAL run's stored window")
    retry_ing_cmd.add_argument("run_id")
    retry_ing_cmd.set_defaults(handler=_retry_ingestion)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
