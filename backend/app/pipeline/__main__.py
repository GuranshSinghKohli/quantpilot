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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    schema_cmd = commands.add_parser("schemas", help="export contract JSON Schemas to docs/schemas/")
    schema_cmd.add_argument("--check", action="store_true", help="fail if docs/schemas/ is stale")
    schema_cmd.set_defaults(handler=_schemas)

    lake_cmd = commands.add_parser("lake", help="object store: init (bucket + versioning) or check")
    lake_cmd.add_argument("action", choices=["init", "check"])
    lake_cmd.set_defaults(handler=_lake)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
