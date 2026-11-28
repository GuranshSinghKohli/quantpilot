"""Market CLI: ``python -m app.market <command>`` (run from ``backend/``)."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.market import CALCULATION_VERSION


def _sessions():
    from app.core.db import SessionLocal, engine
    from app.core.migrations import migration_state

    state = migration_state(engine)
    if not state.is_current:
        raise SystemExit(
            f"database is at revision {state.current}, head is {state.head}; run migrations first"
        )
    return SessionLocal


def _dump(payload: object) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _events_from_news(_args: argparse.Namespace) -> int:
    from app.market.candidates import propose_news_candidates

    db = _sessions()()
    try:
        report = propose_news_candidates(db)
        db.commit()
        return _dump(report)
    finally:
        db.close()


def _accept_resolved_news(_args: argparse.Namespace) -> int:
    from app.market.candidates import accept_resolved_news_candidates

    db = _sessions()()
    try:
        report = accept_resolved_news_candidates(db)
        db.commit()
        return _dump(report)
    finally:
        db.close()


def _impact(args: argparse.Namespace) -> int:
    from app.market.calculator import EventImpactCalculator

    db = _sessions()()
    job_id = uuid.uuid4()
    try:
        calc = EventImpactCalculator(calculation_version=CALCULATION_VERSION)
        if args.event_id:
            report = calc.calculate_event(db, uuid.UUID(args.event_id), job_id=job_id)
        else:
            report = calc.calculate_batch(
                db,
                since=_parse_date(args.since),
                until=_parse_date(args.until),
                job_id=job_id,
            )
        db.commit()
        return _dump(report)
    finally:
        db.close()


def _quality(_args: argparse.Namespace) -> int:
    from app.market.quality import market_quality

    db = _sessions()()
    try:
        return _dump(market_quality(db))
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.market",
        description="QuantPilot market and event intelligence",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("events-from-news", help="Propose NEWS candidates from canonical articles with tickers").set_defaults(
        func=_events_from_news
    )
    sub.add_parser(
        "accept-resolved-news",
        help="Confirm pending NEWS candidates whose provider tickers resolve to an existing entity",
    ).set_defaults(func=_accept_resolved_news)
    impact = sub.add_parser("impact", help="Calculate event-window analytics for confirmed events")
    impact.add_argument("event_id", nargs="?", help="confirmed event UUID")
    impact.add_argument("--since", help="UTC date or timestamp inclusive lower bound")
    impact.add_argument("--until", help="UTC date or timestamp inclusive upper bound")
    impact.set_defaults(func=_impact)
    sub.add_parser("quality", help="Read-only event/market/impact quality report").set_defaults(func=_quality)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
