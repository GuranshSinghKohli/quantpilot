"""Graph CLI: ``python -m app.graph <command>`` (run from ``backend/``)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from app.graph.errors import GraphUnavailable


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


def _status(_args: argparse.Namespace) -> int:
    from app.graph.status import graph_status

    db = _sessions()()
    try:
        return _dump(graph_status(db))
    finally:
        db.close()


def _check(_args: argparse.Namespace) -> int:
    from app.graph.status import graph_check

    db = _sessions()()
    try:
        report = graph_check(db)
        _dump(report)
        return 0 if report.get("ok") else 1
    finally:
        db.close()


def _project(args: argparse.Namespace) -> int:
    from app.graph.projector import project
    from app.graph.store import probe_neo4j

    if probe_neo4j() != "ok":
        print(json.dumps({"ok": False, "error": "Neo4j is unavailable; Postgres was not changed"}))
        return 2
    db = _sessions()()
    try:
        return _dump(project(db))
    except GraphUnavailable as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    finally:
        db.close()


def _reconcile(_args: argparse.Namespace) -> int:
    from app.graph.reconcile import reconcile, reconcile_summary

    db = _sessions()()
    try:
        summary = reconcile_summary(reconcile(db))
        _dump(summary)
        return 0 if summary.get("status") in {"OK", "UNAVAILABLE"} else 1
    finally:
        db.close()


def _repair(args: argparse.Namespace) -> int:
    from app.graph.reconcile import repair
    from app.graph.store import probe_neo4j

    if args.allow_delete and not args.confirm_delete:
        print(json.dumps({"ok": False, "error": "--allow-delete requires --confirm-delete"}))
        return 2
    if probe_neo4j() != "ok":
        print(json.dumps({"ok": False, "error": "Neo4j is unavailable; Postgres was not changed"}))
        return 2
    db = _sessions()()
    try:
        return _dump(
            repair(db, allow_delete=args.allow_delete, confirm_delete=args.confirm_delete)
        )
    except (GraphUnavailable, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    finally:
        db.close()


def _quality(_args: argparse.Namespace) -> int:
    from app.graph.quality import graph_quality

    db = _sessions()()
    try:
        return _dump(graph_quality(db))
    finally:
        db.close()


def _seed(_args: argparse.Namespace) -> int:
    from app.graph.fixtures import seed_fixtures

    db = _sessions()()
    try:
        return _dump(seed_fixtures(db))
    finally:
        db.close()


def _schema(_args: argparse.Namespace) -> int:
    from app.graph.schema import ensure_schema, schema_status
    from app.graph.store import probe_neo4j

    if probe_neo4j() != "ok":
        print(json.dumps({"ok": False, "error": "Neo4j is unavailable"}))
        return 2
    ensure_schema()
    return _dump(schema_status())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.graph", description="QuantPilot graph operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=_status)
    sub.add_parser("check").set_defaults(func=_check)
    sub.add_parser("project").set_defaults(func=_project)
    sub.add_parser("reconcile").set_defaults(func=_reconcile)
    repair = sub.add_parser("repair")
    repair.add_argument(
        "--allow-delete",
        action="store_true",
        help="also delete extra Neo4j nodes/edges (requires --confirm-delete)",
    )
    repair.add_argument(
        "--confirm-delete",
        action="store_true",
        help="required safety flag when --allow-delete is set",
    )
    repair.set_defaults(func=_repair)
    sub.add_parser("quality").set_defaults(func=_quality)
    sub.add_parser("seed-fixtures").set_defaults(func=_seed)
    sub.add_parser("schema").set_defaults(func=_schema)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
