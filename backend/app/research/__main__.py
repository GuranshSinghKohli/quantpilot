"""Research CLI: ``python -m app.research evaluate`` (run from ``backend/``)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def _evaluate(_args: argparse.Namespace) -> int:
    from sqlalchemy.orm import sessionmaker

    from app.core.db import build_engine
    from app.core.migrations import upgrade_to_head
    from app.research.evaluate import evaluate_research

    engine = build_engine("sqlite://")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        print(json.dumps(evaluate_research(db), indent=2, default=str))
        return 0
    finally:
        db.close()
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.research", description="QuantPilot research agents")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("evaluate", help="Development-set research metrics with FakeLLMProvider").set_defaults(
        func=_evaluate
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
