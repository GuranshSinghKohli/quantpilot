"""Load Massive 1-minute session bars into league.bars.

The client is the existing SourceClient, paced at MARKET_REQUESTS_PER_MINUTE
(default 5). A repeat run inserts nothing new. The database must be local
quantpilot. The API key is never printed.

Example:
  backend/.venv/bin/python scripts/backfill_bars.py \\
    --start 2026-11-24 --end 2026-11-24 \\
    --universe backend/tests/fixtures/universe_small.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.settings import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.league.local_url import assert_localhost_database_url  # noqa: E402
from app.league.market.massive_data import (  # noqa: E402
    MarketKeyMissing,
    MassiveResponseError,
    fetch_minute_bars,
    resolve_tickers,
    build_massive_client,
)
from app.league.repo.bars import upsert_bars  # noqa: E402
from app.league.sim.xnys import XNYSCalendar  # noqa: E402
from app.pipeline.errors import RetryExhausted  # noqa: E402
from app.pipeline.sources.http import PermanentSourceError  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill league.bars from Massive minute aggregates.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument(
        "--tickers",
        action="append",
        default=[],
        help="Comma-separated symbols. Repeat the flag to add more.",
    )
    parser.add_argument("--universe", help="CSV with columns ticker,name,sector,market_cap")
    return parser.parse_args(argv)


def _symbols(args: argparse.Namespace) -> tuple[str, ...]:
    raw: list[str] = []
    for chunk in args.tickers:
        raw.extend(part.strip() for part in chunk.split(",") if part.strip())
    return resolve_tickers(raw, args.universe)


def _print_summary(
    tickers: tuple[str, ...],
    inserted: int,
    skipped: dict[str, int],
    calls: int,
    failed: list[str],
    seconds: float,
    per_minute: float,
) -> None:
    print(f"tickers: {len(tickers)}")
    print(f"bars inserted: {inserted}")
    print("bars skipped:")
    if skipped:
        for reason, count in skipped.items():
            print(f"  {reason}: {count}")
    else:
        print("  (none)")
    if failed:
        print("tickers failed: " + ", ".join(failed))
    print(f"calls: {calls}")
    print(f"requests_per_minute: {per_minute}")
    print(f"runtime_seconds: {seconds:.1f}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        print("start and end must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if end < start:
        print("end is before start", file=sys.stderr)
        return 2
    try:
        tickers = _symbols(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    settings = get_settings()
    try:
        assert_localhost_database_url(settings.DATABASE_URL)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        client = build_massive_client(settings)
    except MarketKeyMissing as exc:
        print(str(exc), file=sys.stderr)
        return 2

    calendar = XNYSCalendar()
    base_url = settings.MARKET_BASE_URL
    host = urlsplit(base_url).hostname or ""
    print(f"provider host: {host}", flush=True)
    print(f"window: {start.isoformat()} .. {end.isoformat()}", flush=True)
    inserted = 0
    calls = 0
    skipped: dict[str, int] = {}
    failed: list[str] = []
    started = time.perf_counter()
    session = SessionLocal()
    try:
        for ticker in tickers:
            try:
                result = fetch_minute_bars(
                    client, ticker, start, end, calendar, base_url=base_url
                )
                wrote = upsert_bars(session, result.bars)
                session.commit()
            except PermanentSourceError as exc:
                session.rollback()
                if exc.fatal_for_run or exc.status in (401, 403):
                    print(f"{ticker} failed {exc.kind} status={exc.status}", flush=True)
                    _print_summary(
                        tickers, inserted, skipped, calls, failed,
                        time.perf_counter() - started, settings.MARKET_REQUESTS_PER_MINUTE,
                    )
                    return 1
                failed.append(ticker)
                print(f"{ticker} failed {exc.kind} status={exc.status}", flush=True)
                continue
            except RetryExhausted:
                session.rollback()
                print(f"{ticker} failed after retries", flush=True)
                _print_summary(
                    tickers, inserted, skipped, calls, failed,
                    time.perf_counter() - started, settings.MARKET_REQUESTS_PER_MINUTE,
                )
                return 1
            except MassiveResponseError as exc:
                session.rollback()
                print(f"{ticker} refused: {exc}", flush=True)
                _print_summary(
                    tickers, inserted, skipped, calls, failed,
                    time.perf_counter() - started, settings.MARKET_REQUESTS_PER_MINUTE,
                )
                return 1
            calls += result.calls
            for reason, count in result.skipped.items():
                skipped[reason] = skipped.get(reason, 0) + count
            already = len(result.bars) - wrote
            if already:
                skipped["already_present"] = skipped.get("already_present", 0) + already
            inserted += wrote
            print(
                f"{ticker} inserted={wrote} session_bars={len(result.bars)} "
                f"skipped={dict(result.skipped) or '-'}",
                flush=True,
            )
    finally:
        session.close()
        client.close()
    _print_summary(
        tickers, inserted, skipped, calls, failed,
        time.perf_counter() - started, settings.MARKET_REQUESTS_PER_MINUTE,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
