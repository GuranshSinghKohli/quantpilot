"""Replay one XNYS session from bars already in league.bars.

No fetch and no invented price. The default run rolls back, including the
throwaway public.users row. --keep commits only when every invariant passed.

Example:
  backend/.venv/bin/python scripts/replay_day.py \\
    --date 2026-11-24 \\
    --universe backend/tests/fixtures/universe_small.csv \\
    --risk-level balanced \\
    --seed 42
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.settings import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.league.config import LeagueSettings  # noqa: E402
from app.league.local_url import assert_localhost_database_url  # noqa: E402
from app.league.sim.calendar import CalendarUnavailable  # noqa: E402
from app.league.sim.replay import (  # noqa: E402
    ReplayInvariantFailed,
    ReplayRefused,
    format_summary,
    replay_day,
    require_session,
)
from app.league.sim.xnys import XNYSCalendar  # noqa: E402
from app.league.universe import UniverseError, load_universe  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay one session from league.bars.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD session date")
    parser.add_argument("--universe", required=True, help="CSV with columns ticker,name,sector,market_cap")
    parser.add_argument("--risk-level", default="balanced", choices=("conservative", "balanced", "aggressive"))
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--keep", action="store_true", help="Commit the books. Default is rollback.")
    return parser.parse_args(argv)


def _settings() -> LeagueSettings:
    """Money numbers from the spec, not from the environment."""
    return LeagueSettings(
        STARTING_CASH=Decimal("1000"),
        COMMISSION_USD=Decimal("0"),
        MIN_ORDER_USD=Decimal("5"),
        SLIPPAGE_BPS=5,
        FILL_TIMEOUT_MINUTES=30,
        DATA_DELAY_MINUTES=0,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        day = date.fromisoformat(args.date)
    except ValueError:
        print("date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        universe = load_universe(args.universe)
    except UniverseError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    app_settings = get_settings()
    try:
        assert_localhost_database_url(app_settings.DATABASE_URL)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    calendar = XNYSCalendar()
    try:
        opened, closed = require_session(calendar, day)
    except (ReplayRefused, CalendarUnavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = _settings()
    started = time.perf_counter()
    session = SessionLocal()
    try:
        try:
            summary = replay_day(
                session,
                opened=opened,
                closed=closed,
                universe=universe,
                risk_level=args.risk_level,
                seed=args.seed,
                settings=settings,
                calendar=calendar,
                starting_cash=settings.STARTING_CASH,
            )
        except ReplayRefused as exc:
            session.rollback()
            print(str(exc), file=sys.stderr)
            return 2
        except ReplayInvariantFailed as exc:
            session.rollback()
            print(str(exc), file=sys.stderr)
            return 1
        print(format_summary(summary, seed=args.seed, day=day, risk=args.risk_level))
        print(f"runtime_seconds: {time.perf_counter() - started:.1f}")
        if args.keep:
            session.commit()
        else:
            session.rollback()
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
