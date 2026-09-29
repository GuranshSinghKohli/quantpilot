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
    producer = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings))
    consumer = build_consumer(settings, settings.KAFKA_CONSUMER_GROUP_RESEARCH)
    consumer.subscribe([settings.KAFKA_TOPIC_RESEARCH_REQUEST])
    try:
        serve(SessionLocal, consumer, producer, settings)
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
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
