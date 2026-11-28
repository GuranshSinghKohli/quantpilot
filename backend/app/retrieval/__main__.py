"""Retrieval CLI: ``python -m app.retrieval <command>`` (run from ``backend/``)."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import List, Optional


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


def _documents(_args: argparse.Namespace) -> int:
    from app.retrieval.documents import index_documents

    db = _sessions()()
    try:
        report = index_documents(db)
        db.commit()
        return _dump(report)
    finally:
        db.close()


def _embed(args: argparse.Namespace) -> int:
    from app.retrieval.store import embed_documents

    db = _sessions()()
    try:
        report = embed_documents(
            db,
            document_id=uuid.UUID(args.document) if args.document else None,
        )
        db.commit()
        return _dump(report)
    finally:
        db.close()


def _evaluate(_args: argparse.Namespace) -> int:
    from app.core.db import build_engine
    from app.core.migrations import upgrade_to_head
    from sqlalchemy.orm import sessionmaker
    from app.retrieval.evaluate import evaluate_retrieval

    engine = build_engine("sqlite://")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        return _dump(evaluate_retrieval(db))
    finally:
        db.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.retrieval",
        description="QuantPilot evidence retrieval",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("documents", help="Normalize sources/news into retrieval documents").set_defaults(func=_documents)
    embed = sub.add_parser("embed", help="Embed pending chunks (skips unchanged content/model)")
    embed.add_argument("--document", help="retrieval document UUID")
    embed.set_defaults(func=_embed)
    sub.add_parser("evaluate", help="Development-set recall/precision with the fake provider").set_defaults(
        func=_evaluate
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
