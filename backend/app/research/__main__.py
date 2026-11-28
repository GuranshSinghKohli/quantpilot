"""Research CLI: ``python -m app.research evaluate`` (run from ``backend/``)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def _evaluate(args: argparse.Namespace) -> int:
    from sqlalchemy.orm import sessionmaker

    from app.core.db import build_engine
    from app.core.migrations import upgrade_to_head
    from app.research.quality import evaluate_suite

    engine = build_engine("sqlite://")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        suite = args.suite or "baseline"
        print(json.dumps(evaluate_suite(db, suite), indent=2, default=str))
        return 0
    finally:
        db.close()
        engine.dispose()


def _worker(_args: argparse.Namespace) -> int:
    from app.core.db import SessionLocal
    from app.core.settings import get_settings
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.consumer import build_consumer
    from app.pipeline.kafka.producer import EventProducer
    from app.pipeline.retry import RetryPolicy
    from app.research.worker import serve

    settings = get_settings()
    settings.validate_for_environment()
    from app.observability.instrumentation import configure_langsmith, configure_opentelemetry

    configure_langsmith()
    configure_opentelemetry(None)
    producer = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
    consumer = build_consumer(settings, settings.KAFKA_CONSUMER_GROUP_RESEARCH)
    consumer.subscribe([settings.KAFKA_TOPIC_RESEARCH_REQUEST])
    try:
        # idle_timeout_s=0 keeps the ECS task running when the request topic is empty.
        # The default of 30s is for tests and the load harness, which pass their own timeout.
        serve(SessionLocal, consumer, producer, settings, idle_timeout_s=0)
    finally:
        consumer.close()
        producer.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.research", description="QuantPilot research agents")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate", help="Development-set research metrics with FakeLLMProvider")
    evaluate.add_argument(
        "--suite",
        default="baseline",
        choices=["baseline", "retrieval", "claims", "reports", "planner", "async", "security", "all"],
        help="baseline keeps the 24-case report; other suites are fixture checks",
    )
    evaluate.set_defaults(func=_evaluate)
    sub.add_parser(
        "worker",
        help="Publish the research outbox and consume quantpilot.research.request",
    ).set_defaults(func=_worker)
    load = sub.add_parser(
        "load",
        help="Development load test against disposable Postgres and test.* Kafka topics. Uses FakeLLM.",
    )
    load.add_argument(
        "--database-url",
        default=None,
        help="Must be postgresql://...@localhost:55432/quantpilot_load",
    )
    load.add_argument("--kafka", default=None, help="Broker bootstrap, or TEST_KAFKA_BOOTSTRAP")
    load.add_argument("--requests", type=int, default=100)
    load.add_argument("--concurrency", default="1,5,10,25,50")
    load.add_argument("--scaling-workers", default="1,2,4")
    load.add_argument("--scaling-concurrency", type=int, default=10)
    load.add_argument("--output", default="/tmp/quantpilot-research-load.json")
    load.add_argument("--skip-scenarios", action="store_true")
    load.add_argument("--skip-matrix", action="store_true")
    load.set_defaults(func=_load)
    return parser


def _load(args: argparse.Namespace) -> int:
    import logging
    import os

    logging.getLogger().setLevel(logging.WARNING)
    from app.research.loadtest import default_database_url, run_load

    database_url = args.database_url or default_database_url()
    kafka = args.kafka or os.environ.get("TEST_KAFKA_BOOTSTRAP", "").strip() or "localhost:9092"
    concurrency = [int(part) for part in args.concurrency.split(",") if part.strip()]
    scaling = [int(part) for part in args.scaling_workers.split(",") if part.strip()]
    report = run_load(
        database_url=database_url,
        kafka_bootstrap=kafka,
        requests=args.requests,
        concurrency=concurrency,
        scaling_workers=scaling,
        scaling_concurrency=args.scaling_concurrency,
        include_scenarios=not args.skip_scenarios,
        include_matrix=not args.skip_matrix,
    )
    payload = json.dumps(report, indent=2, default=str)
    output = args.output
    if output:
        parent = os.path.dirname(output)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        print(f"WROTE {output}", flush=True)
    _print_load_table(report)
    forbidden = (report.get("cardinality") or {}).get("forbidden_label_keys") or []
    if forbidden:
        print(f"FORBIDDEN METRIC LABELS {forbidden}", flush=True)
        return 1
    return 0


def _print_load_table(report: dict) -> None:
    print(
        "These results characterize the tested development environment and are not production capacity guarantees.",
        flush=True,
    )
    print("DEVELOPMENT LOAD TEST / FAKELLM / FIXTURE-BASED", flush=True)
    print("| Workers | Concurrency | Requests | Completed | Failed | p50 | p95 | p99 | Throughput |", flush=True)
    print("|---------|-------------|----------|-----------|--------|-----|-----|-----|------------|", flush=True)
    for level in list(report.get("matrix") or []) + list(report.get("scaling") or []):
        e2e = level.get("end_to_end_s") or {}
        print(
            "| {workers} | {concurrency} | {requests} | {completed} | {failed} | {p50} | {p95} | {p99} | {throughput} |".format(
                workers=level.get("workers"),
                concurrency=level.get("concurrency"),
                requests=level.get("requests"),
                completed=level.get("completed"),
                failed=level.get("failed"),
                p50=e2e.get("p50"),
                p95=e2e.get("p95"),
                p99=e2e.get("p99"),
                throughput=level.get("completion_throughput_jobs_per_s"),
            ),
            flush=True,
        )
    print("| Concurrency | Queue p95 | Kafka Lag | DB Errors | Duplicate Claims |", flush=True)
    print("|-------------|-----------|-----------|-----------|------------------|", flush=True)
    for level in report.get("matrix") or []:
        queue = (level.get("queue_wait_s") or {}).get("p95")
        lag = ((level.get("kafka") or {}).get("consumer_lag") or {}).get("peak_lag")
        database = level.get("database") or {}
        db_errors = (database.get("connection_errors") or 0) + (database.get("deadlock_delta") or 0)
        dupes = level.get("duplicate_claim_statements")
        print(
            f"| {level.get('concurrency')} | {queue} | {lag} | {db_errors} | {dupes} |",
            flush=True,
        )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
