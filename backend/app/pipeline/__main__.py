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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    schema_cmd = commands.add_parser("schemas", help="export contract JSON Schemas to docs/schemas/")
    schema_cmd.add_argument("--check", action="store_true", help="fail if docs/schemas/ is stale")
    schema_cmd.set_defaults(handler=_schemas)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
