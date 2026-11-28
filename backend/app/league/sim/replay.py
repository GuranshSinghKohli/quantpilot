"""One-day paper replay. Reads league.bars. Does not fetch or invent a price.

Decisions, stops, and equity use latest_known_prices: a bar timestamped t is
known only once t + 1 minute has passed. Fills stay on the bar open, the first
in-session bar with t > order time and t <= now.
"""

from __future__ import annotations

import random
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.league.config import LeagueSettings
from app.league.models import Agent, AgentCycle, Bar, Fill, League, Order, Player, Rationale, Season
from app.league.repo import create_order, create_portfolio, get_portfolio, latest_known_prices
from app.league.sim.clock import ManualClock
from app.league.sim.process import process_order
from app.league.sim.risk import (
    Dropped,
    Exclusions,
    PlayerOrder,
    PortfolioSnapshot,
    PositionSnapshot,
    Proposal,
    RiskOrder,
    apply_risk,
    check_stop_losses,
    validate_player_order,
)
from app.league.universe import Universe

_NY = ZoneInfo("America/New_York")
_CASH = Decimal("0.0001")
_RETURN = Decimal("0.00000001")
_STEP = timedelta(minutes=5)
_BUY_PCTS = (Decimal("5"), Decimal("8"), Decimal("10"))
_SELL_PCTS = (Decimal("25"), Decimal("50"), Decimal("100"))
_CONFIDENCE = (Decimal("0.40"), Decimal("0.70"), Decimal("0.90"))
_SCHEDULED = ((10, 0), (12, 30), (15, 30))
_STATUSES = ("accepted", "queued", "filled", "rejected", "expired", "cancelled")
_ACTIVE = ("accepted", "queued")
_TERMINAL = frozenset({"filled", "rejected", "expired", "cancelled"})
_LEGAL = frozenset({"pending_approval", "accepted", "rejected", "queued", "filled", "expired", "cancelled"})


class ReplayRefused(RuntimeError):
    """The day cannot be replayed. Nothing was fetched and no price was invented."""


class ReplayInvariantFailed(RuntimeError):
    """A book broke an invariant. The caller rolls the transaction back."""


def require_session(calendar, day: date) -> tuple[datetime, datetime]:
    """[open, close) for an XNYS session date. A closed day is refused."""
    bounds = calendar.session_bounds(day)
    if bounds is None:
        raise ReplayRefused(f"{day.isoformat()} is not an XNYS session")
    return bounds


def missing_session_tickers(
    session: Session,
    tickers: tuple[str, ...],
    opened: datetime,
    closed: datetime,
) -> tuple[str, ...]:
    """Universe tickers with no bar in [opened, close)."""
    if not tickers:
        return ()
    have = set(
        session.scalars(
            select(Bar.ticker)
            .where(Bar.ticker.in_(list(tickers)), Bar.t >= opened, Bar.t < closed)
            .distinct()
        ).all()
    )
    return tuple(ticker for ticker in tickers if ticker not in have)


def replay_day(
    session: Session,
    *,
    opened: datetime,
    closed: datetime,
    universe: Universe,
    risk_level: str,
    seed: int,
    settings: LeagueSettings,
    calendar,
    starting_cash: Decimal,
) -> "ReplaySummary":
    """Run one session. The caller owns the transaction and decides commit or rollback."""
    if risk_level not in ("conservative", "balanced", "aggressive"):
        raise ReplayRefused(f"unknown risk level {risk_level!r}")
    if starting_cash <= 0:
        raise ReplayRefused("starting cash must be positive")
    tickers = tuple(listing.ticker for listing in universe.listings)
    missing = missing_session_tickers(session, tickers, opened, closed)
    if missing:
        names = ", ".join(missing)
        raise ReplayRefused(
            f"no in-session bars for {names}. Run scripts/backfill_bars.py. This replay does not fetch."
        )
    agent_id, actual_id, ghost_id = _prepare(
        session,
        tickers=tickers,
        risk_level=risk_level,
        starting_cash=starting_cash,
        opened=opened,
        closed=closed,
    )
    portfolio_ids = (actual_id, ghost_id)
    rng = random.Random(seed)
    cycles, player_times = _times(rng, opened, closed)
    interest = tickers
    clock = ManualClock(opened)
    terminal: dict[uuid.UUID, str] = {}
    dropped: Counter[str] = Counter()
    player_orders = 0
    moment = opened
    while moment < closed:
        if calendar.is_open(moment):
            if moment in cycles:
                _agent_cycle(
                    session,
                    agent_id,
                    portfolio_ids,
                    tickers,
                    interest,
                    universe,
                    risk_level,
                    settings,
                    calendar,
                    rng,
                    moment,
                    dropped,
                )
            if moment in player_times:
                if _player_order(
                    session, actual_id, tickers, universe, settings, calendar, rng, moment
                ):
                    player_orders += 1
        nxt = min(moment + _STEP, closed)
        clock.advance(nxt - clock.now())
        moment = clock.now()
        _process(session, portfolio_ids, clock, calendar, settings)
        _stops(session, portfolio_ids, risk_level, calendar, clock.now())
        _assert_books(
            session,
            portfolio_ids,
            clock,
            calendar,
            settings,
            starting_cash,
            terminal,
            player_orders,
        )
    return _summary(
        session,
        portfolio_ids,
        opened,
        closed,
        starting_cash,
        dropped,
        player_orders,
    )


def format_summary(summary: "ReplaySummary", *, seed: int, day: date, risk: str) -> str:
    """Stable text. Runtime is not included, and neither is any id."""
    lines = [
        f"seed: {seed}",
        f"date: {day.isoformat()}",
        f"risk: {risk}",
        "orders: " + " ".join(f"{status}={count}" for status, count in summary.orders_by_status),
        f"fills: {summary.fills}",
        "rejected: " + _reason_text(summary.rejected),
        "expired: " + _reason_text(summary.expired),
        "dropped: " + _reason_text(summary.dropped),
        f"player_orders: {summary.player_orders}",
        f"actual_cash: {_dec(summary.actual_cash)}",
        f"actual_equity: {_dec(summary.actual_equity)}",
        f"actual_return: {_dec(summary.actual_return)}",
        f"ghost_cash: {_dec(summary.ghost_cash)}",
        f"ghost_equity: {_dec(summary.ghost_equity)}",
        f"ghost_return: {_dec(summary.ghost_return)}",
        f"spy_return: {_dec(summary.spy_return)}",
        f"invariants: {summary.invariants}",
    ]
    return "\n".join(lines)


class ReplaySummary:
    def __init__(
        self,
        *,
        orders_by_status: tuple[tuple[str, int], ...],
        fills: int,
        rejected: tuple[tuple[str, int], ...],
        expired: tuple[tuple[str, int], ...],
        dropped: tuple[tuple[str, int], ...],
        player_orders: int,
        actual_cash: Decimal,
        actual_equity: Decimal,
        actual_return: Decimal,
        ghost_cash: Decimal,
        ghost_equity: Decimal,
        ghost_return: Decimal,
        spy_return: Decimal | None,
        invariants: str,
    ) -> None:
        self.orders_by_status = orders_by_status
        self.fills = fills
        self.rejected = rejected
        self.expired = expired
        self.dropped = dropped
        self.player_orders = player_orders
        self.actual_cash = actual_cash
        self.actual_equity = actual_equity
        self.actual_return = actual_return
        self.ghost_cash = ghost_cash
        self.ghost_equity = ghost_equity
        self.ghost_return = ghost_return
        self.spy_return = spy_return
        self.invariants = invariants

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReplaySummary):
            return NotImplemented
        return self.__dict__ == other.__dict__


def _dec(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    return format(value, "f")


def _reason_text(rows: tuple[tuple[str, int], ...]) -> str:
    if not rows:
        return "(none)"
    return ", ".join(f"{reason}={count}" for reason, count in rows)


def _times(rng: random.Random, opened: datetime, closed: datetime) -> tuple[set[datetime], set[datetime]]:
    grid: list[datetime] = []
    moment = opened
    while moment < closed:
        grid.append(moment)
        moment += _STEP
    scheduled = {
        moment
        for moment in grid
        if (moment.astimezone(_NY).hour, moment.astimezone(_NY).minute) in _SCHEDULED
    }
    pool = [moment for moment in grid if moment not in scheduled and moment != opened]
    extras = rng.sample(pool, k=min(2, len(pool)))
    player_pool = [moment for moment in grid if moment != opened]
    players = rng.sample(player_pool, k=min(2, len(player_pool)))
    return scheduled | set(extras), set(players)


def _prepare(
    session: Session,
    *,
    tickers: tuple[str, ...],
    risk_level: str,
    starting_cash: Decimal,
    opened: datetime,
    closed: datetime,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = session.execute(
        text(
            """
            INSERT INTO public.users (email, hashed_password, created_at)
            VALUES (:email, 'not-a-login', TIMESTAMPTZ '2026-11-24 14:00:00+00')
            RETURNING id
            """
        ),
        {"email": f"league-replay-{uuid.uuid4()}@local.quantpilot"},
    ).scalar_one()
    player = Player(user_id=user_id, display_name="Replay", avatar_id="avatar")
    session.add(player)
    session.flush()
    league = League(name="Replay", is_public=False, owner_id=player.id)
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        number=1,
        starts_at=opened,
        ends_at=closed,
        starting_cash=starting_cash,
        status="active",
    )
    session.add(season)
    session.flush()
    agent = Agent(
        player_id=player.id,
        league_id=league.id,
        name="Replay",
        risk_level=risk_level,
        style="momentum",
        interests=list(tickers),
        mode="autopilot",
        config_version=1,
    )
    session.add(agent)
    session.flush()
    actual = create_portfolio(session, agent.id, season.id, "actual", starting_cash)
    ghost = create_portfolio(session, agent.id, season.id, "ghost", starting_cash)
    return agent.id, actual.id, ghost.id


def _snapshot(session: Session, portfolio_id: uuid.UUID) -> PortfolioSnapshot:
    view = get_portfolio(session, portfolio_id)
    return PortfolioSnapshot(
        cash=view.portfolio.cash,
        positions=tuple(
            PositionSnapshot(ticker=row.ticker, qty=row.qty, avg_cost=row.avg_cost)
            for row in view.positions
        ),
    )


def _held(session: Session, portfolio_id: uuid.UUID) -> list[str]:
    return [row.ticker for row in get_portfolio(session, portfolio_id).positions]


def _proposals(
    rng: random.Random,
    tickers: tuple[str, ...],
    held: list[str],
    prices: dict[str, Decimal],
) -> list[Proposal]:
    quoted = sorted(ticker for ticker in tickers if ticker in prices)
    if not quoted:
        return []
    proposals: list[Proposal] = []
    held_quoted = sorted({ticker for ticker in held if ticker in prices})
    if held_quoted:
        proposals.append(
            Proposal(
                ticker=rng.choice(held_quoted),
                action="sell",
                size_pct_of_equity=rng.choice(_SELL_PCTS),
                confidence=Decimal("0.80"),
            )
        )
    while len(proposals) < 3:
        proposals.append(
            Proposal(
                ticker=rng.choice(quoted),
                action="buy",
                size_pct_of_equity=rng.choice(_BUY_PCTS),
                confidence=rng.choice(_CONFIDENCE),
            )
        )
    return proposals


def _agent_cycle(
    session: Session,
    agent_id: uuid.UUID,
    portfolio_ids: tuple[uuid.UUID, uuid.UUID],
    tickers: tuple[str, ...],
    interest: tuple[str, ...],
    universe: Universe,
    risk_level: str,
    settings: LeagueSettings,
    calendar,
    rng: random.Random,
    when: datetime,
    dropped: Counter[str],
) -> None:
    session.expire_all()
    prices = latest_known_prices(session, tickers, when)
    held: list[str] = []
    for portfolio_id in portfolio_ids:
        held.extend(_held(session, portfolio_id))
    proposals = _proposals(rng, tickers, held, prices)
    if not proposals:
        return
    cycle: AgentCycle | None = None
    day = when.astimezone(_NY).date()
    for portfolio_id in portfolio_ids:
        snapshot = _snapshot(session, portfolio_id)
        orders, skipped = apply_risk(
            proposals,
            snapshot,
            prices,
            risk_level,
            universe,
            interest,
            Exclusions(),
            _buys_today(session, portfolio_id, day),
            settings,
        )
        for item in skipped:
            dropped[item.reason] += 1
        if not orders:
            continue
        if cycle is None:
            cycle = AgentCycle(
                agent_id=agent_id,
                trigger="scheduled",
                started_at=when,
                finished_at=when,
                status="finished",
            )
            session.add(cycle)
            session.flush()
        for order in orders:
            rationale = Rationale(
                cycle_id=cycle.id,
                summary=order.summary or f"{order.side} {order.ticker}",
                confidence=Decimal("0.50"),
                news_ids=["n1"],
                graph_path=["desk"],
                validated=True,
                system=False,
            )
            session.add(rationale)
            session.flush()
            _insert(session, portfolio_id, order, when, calendar, source="agent", rationale_id=rationale.id, cycle_id=cycle.id)


def _player_order(
    session: Session,
    portfolio_id: uuid.UUID,
    tickers: tuple[str, ...],
    universe: Universe,
    settings: LeagueSettings,
    calendar,
    rng: random.Random,
    when: datetime,
) -> bool:
    session.expire_all()
    prices = latest_known_prices(session, tickers, when)
    quoted = sorted(ticker for ticker in tickers if ticker in prices)
    if not quoted:
        return False
    ticker = rng.choice(quoted)
    decided = validate_player_order(
        PlayerOrder(ticker=ticker, side="buy", notional_usd=Decimal("50")),
        _snapshot(session, portfolio_id),
        prices,
        universe,
        settings,
    )
    if isinstance(decided, Dropped):
        return False
    if not isinstance(decided, RiskOrder):
        return False
    _insert(session, portfolio_id, decided, when, calendar, source="player", rationale_id=None, cycle_id=None)
    return True


def _insert(
    session: Session,
    portfolio_id: uuid.UUID,
    order: RiskOrder,
    when: datetime,
    calendar,
    *,
    source: str,
    rationale_id: uuid.UUID | None,
    cycle_id: uuid.UUID | None,
) -> None:
    status = "accepted" if calendar.is_open(when) else "queued"
    if order.side == "buy":
        notional, qty = order.notional_usd, None
    else:
        notional, qty = None, order.qty
    create_order(
        session,
        portfolio_id=portfolio_id,
        ticker=order.ticker,
        side=order.side,
        source=source,
        status=status,
        game_time=when,
        notional_usd=notional,
        qty=qty,
        rationale_id=rationale_id,
        cycle_id=cycle_id,
    )


def _buys_today(session: Session, portfolio_id: uuid.UUID, day: date) -> int:
    orders = session.scalars(
        select(Order).where(Order.portfolio_id == portfolio_id, Order.source == "agent", Order.side == "buy")
    ).all()
    return sum(1 for order in orders if order.game_time.astimezone(_NY).date() == day)


def _is_system(session: Session, order: Order) -> bool:
    if order.rationale_id is None:
        return False
    rationale = session.get(Rationale, order.rationale_id)
    return bool(rationale and rationale.system)


def _sort_key(session: Session, order: Order) -> tuple:
    size = order.qty if order.qty is not None else order.notional_usd or Decimal("0")
    return (order.game_time, order.ticker, order.side, int(_is_system(session, order)), size, order.source)


def _process(session: Session, portfolio_ids: tuple[uuid.UUID, uuid.UUID], clock: ManualClock, calendar, settings: LeagueSettings) -> None:
    orders = list(
        session.scalars(
            select(Order).where(Order.portfolio_id.in_(portfolio_ids), Order.status.in_(_ACTIVE))
        ).all()
    )
    orders.sort(key=lambda order: _sort_key(session, order))
    for order in orders:
        process_order(session, order.id, clock, calendar, settings)


def _system_sell_pending(session: Session, portfolio_id: uuid.UUID, ticker: str) -> bool:
    orders = session.scalars(
        select(Order).where(
            Order.portfolio_id == portfolio_id,
            Order.ticker == ticker,
            Order.side == "sell",
            Order.status.in_(_ACTIVE),
        )
    ).all()
    return any(_is_system(session, order) for order in orders)


def _stops(
    session: Session,
    portfolio_ids: tuple[uuid.UUID, uuid.UUID],
    risk_level: str,
    calendar,
    when: datetime,
) -> None:
    session.expire_all()
    for portfolio_id in portfolio_ids:
        snapshot = _snapshot(session, portfolio_id)
        prices = latest_known_prices(session, [position.ticker for position in snapshot.positions], when)
        missing = [position.ticker for position in snapshot.positions if position.ticker not in prices]
        if missing:
            raise ReplayInvariantFailed(
                "held ticker has no completed minute: " + ", ".join(missing)
            )
        for order in check_stop_losses(snapshot.positions, prices, risk_level):
            if _system_sell_pending(session, portfolio_id, order.ticker):
                continue
            rationale = Rationale(
                cycle_id=None,
                summary=order.summary or "Stop-loss",
                confidence=Decimal("1.00"),
                news_ids=[],
                graph_path=[],
                validated=True,
                system=True,
            )
            session.add(rationale)
            session.flush()
            _insert(
                session,
                portfolio_id,
                order,
                when,
                calendar,
                source="agent",
                rationale_id=rationale.id,
                cycle_id=None,
            )


def _quantize_debit(amount: Decimal) -> Decimal:
    return amount.quantize(_CASH, rounding=ROUND_CEILING)


def _quantize_credit(amount: Decimal) -> Decimal:
    return amount.quantize(_CASH, rounding=ROUND_FLOOR)


def _cash_from_fills(session: Session, portfolio_id: uuid.UUID, starting_cash: Decimal, commission: Decimal) -> Decimal:
    rows = session.execute(
        select(Order.side, Fill.qty, Fill.price)
        .join(Fill, Fill.order_id == Order.id)
        .where(Order.portfolio_id == portfolio_id)
    ).all()
    cash = starting_cash
    for side, qty, price in rows:
        if side == "buy":
            cash -= _quantize_debit(qty * price + commission)
        else:
            proceeds = qty * price - commission
            cash += _quantize_credit(proceeds) if proceeds >= 0 else -_quantize_debit(-proceeds)
    return cash


def _book(session: Session, portfolio_id: uuid.UUID) -> tuple:
    view = get_portfolio(session, portfolio_id)
    fills = session.execute(
        select(Order.ticker, Order.side, Fill.qty, Fill.price, Fill.bar_time)
        .join(Fill, Fill.order_id == Order.id)
        .where(Order.portfolio_id == portfolio_id)
    ).all()
    positions = tuple(
        sorted((row.ticker, row.qty, row.avg_cost, row.cost_basis) for row in view.positions)
    )
    return (view.portfolio.cash, positions, tuple(sorted(fills)))


def _assert_books(
    session: Session,
    portfolio_ids: tuple[uuid.UUID, uuid.UUID],
    clock: ManualClock,
    calendar,
    settings: LeagueSettings,
    starting_cash: Decimal,
    terminal: dict[uuid.UUID, str],
    player_orders: int,
) -> None:
    now = clock.now()
    cutoff = now - timedelta(minutes=1)
    actual_id, ghost_id = portfolio_ids
    books = []
    for portfolio_id in portfolio_ids:
        session.expire_all()
        view = get_portfolio(session, portfolio_id)
        if view.portfolio.cash < 0:
            raise ReplayInvariantFailed("cash is negative")
        prices = latest_known_prices(session, [row.ticker for row in view.positions], now)
        for position in view.positions:
            if position.qty <= 0:
                raise ReplayInvariantFailed(f"{position.ticker} qty is not positive")
            expected = (position.cost_basis / position.qty).quantize(_CASH, rounding=ROUND_HALF_EVEN)
            if position.avg_cost != expected:
                raise ReplayInvariantFailed(f"{position.ticker} avg_cost does not match cost basis")
            if position.ticker not in prices:
                raise ReplayInvariantFailed(f"{position.ticker} has no completed minute")
        equity = view.portfolio.cash + sum(
            (position.qty * prices[position.ticker] for position in view.positions),
            Decimal("0"),
        )
        sql_equity = session.scalar(
            text(
                """
                SELECT p.cash + COALESCE((
                    SELECT SUM(pos.qty * latest.c)
                    FROM league.positions pos
                    JOIN LATERAL (
                        SELECT c FROM league.bars
                        WHERE ticker = pos.ticker AND t <= :cutoff
                        ORDER BY t DESC
                        LIMIT 1
                    ) latest ON true
                    WHERE pos.portfolio_id = p.id
                ), 0)
                FROM league.portfolios p
                WHERE p.id = :portfolio_id
                """
            ),
            {"cutoff": cutoff, "portfolio_id": portfolio_id},
        )
        if equity != sql_equity:
            raise ReplayInvariantFailed("equity does not match the completed-minute close")
        if view.portfolio.cash != _cash_from_fills(session, portfolio_id, starting_cash, settings.COMMISSION_USD):
            raise ReplayInvariantFailed("cash does not reconcile with fills")
        signed: dict[str, Decimal] = {}
        fill_rows = session.execute(
            select(Order.ticker, Order.side, Fill.qty)
            .join(Fill, Fill.order_id == Order.id)
            .where(Order.portfolio_id == portfolio_id)
        ).all()
        for ticker, side, qty in fill_rows:
            signed[ticker] = signed.get(ticker, Decimal("0")) + (qty if side == "buy" else -qty)
        held = {position.ticker: position.qty for position in view.positions}
        for ticker, qty in signed.items():
            if qty == 0:
                if ticker in held:
                    raise ReplayInvariantFailed(f"{ticker} is still open after a flat fill total")
            elif held.get(ticker) != qty:
                raise ReplayInvariantFailed(f"{ticker} qty does not match buys minus sells")
        if set(held) != {ticker for ticker, qty in signed.items() if qty != 0}:
            raise ReplayInvariantFailed("positions do not match fills")
        orders = session.scalars(select(Order).where(Order.portfolio_id == portfolio_id)).all()
        fill_counts = dict(
            session.execute(
                select(Fill.order_id, func.count())
                .join(Order, Order.id == Fill.order_id)
                .where(Order.portfolio_id == portfolio_id)
                .group_by(Fill.order_id)
            ).all()
        )
        for order in orders:
            if order.status not in _LEGAL:
                raise ReplayInvariantFailed(f"illegal status {order.status}")
            count = int(fill_counts.get(order.id, 0))
            if order.status == "filled" and count != 1:
                raise ReplayInvariantFailed("a filled order does not have one fill")
            if order.status != "filled" and count != 0:
                raise ReplayInvariantFailed("an unfilled order has a fill")
            previous = terminal.get(order.id)
            if previous in _TERMINAL and order.status != previous:
                raise ReplayInvariantFailed("a terminal order changed status")
            if order.status in _TERMINAL:
                terminal[order.id] = order.status
            fill = session.scalar(select(Fill).where(Fill.order_id == order.id))
            if fill is None:
                continue
            if fill.bar_time <= order.game_time:
                raise ReplayInvariantFailed("a fill is at or before its order")
            if not calendar.is_open(fill.bar_time):
                raise ReplayInvariantFailed("a fill is outside the session")
            if fill.bar_time > now:
                raise ReplayInvariantFailed("a fill is after the clock")
        books.append(_book(session, portfolio_id))
    ghost_players = session.scalars(
        select(Order).where(Order.portfolio_id == ghost_id, Order.source == "player")
    ).all()
    if ghost_players:
        raise ReplayInvariantFailed("the ghost book has a player order")
    if player_orders == 0 and books[0] != books[1]:
        raise ReplayInvariantFailed("books diverged with no player order")


def _summary(
    session: Session,
    portfolio_ids: tuple[uuid.UUID, uuid.UUID],
    opened: datetime,
    closed: datetime,
    starting_cash: Decimal,
    dropped: Counter[str],
    player_orders: int,
) -> ReplaySummary:
    session.expire_all()
    orders = session.scalars(select(Order).where(Order.portfolio_id.in_(portfolio_ids))).all()
    by_status = Counter(order.status for order in orders)
    rejected = Counter(order.reject_reason or "(none)" for order in orders if order.status == "rejected")
    expired = Counter(order.reject_reason or "(none)" for order in orders if order.status == "expired")
    fills = session.scalar(
        select(func.count())
        .select_from(Fill)
        .join(Order, Order.id == Fill.order_id)
        .where(Order.portfolio_id.in_(portfolio_ids))
    )
    now = closed
    actual = _equity(session, portfolio_ids[0], now)
    ghost = _equity(session, portfolio_ids[1], now)

    def ret(equity: Decimal) -> Decimal:
        return ((equity - starting_cash) / starting_cash).quantize(_RETURN, rounding=ROUND_HALF_EVEN)

    return ReplaySummary(
        orders_by_status=tuple((status, int(by_status.get(status, 0))) for status in _STATUSES),
        fills=int(fills or 0),
        rejected=tuple(sorted(rejected.items())),
        expired=tuple(sorted(expired.items())),
        dropped=tuple(sorted(dropped.items())),
        player_orders=player_orders,
        actual_cash=actual[0],
        actual_equity=actual[1],
        actual_return=ret(actual[1]),
        ghost_cash=ghost[0],
        ghost_equity=ghost[1],
        ghost_return=ret(ghost[1]),
        spy_return=_spy_return(session, opened, closed),
        invariants="pass",
    )


def _equity(session: Session, portfolio_id: uuid.UUID, now: datetime) -> tuple[Decimal, Decimal]:
    view = get_portfolio(session, portfolio_id)
    prices = latest_known_prices(session, [row.ticker for row in view.positions], now)
    equity = view.portfolio.cash + sum(
        (row.qty * prices[row.ticker] for row in view.positions),
        Decimal("0"),
    )
    return view.portfolio.cash, equity


def _spy_return(session: Session, opened: datetime, closed: datetime) -> Decimal | None:
    """First session open to last session close. This is the day's bar, read after the close."""
    first = session.scalar(
        select(Bar)
        .where(Bar.ticker == "SPY", Bar.t >= opened, Bar.t < closed)
        .order_by(Bar.t.asc())
        .limit(1)
    )
    last = session.scalar(
        select(Bar)
        .where(Bar.ticker == "SPY", Bar.t >= opened, Bar.t < closed)
        .order_by(Bar.t.desc())
        .limit(1)
    )
    if first is None or last is None or first.o == 0:
        return None
    return ((last.c - first.o) / first.o).quantize(_RETURN, rounding=ROUND_HALF_EVEN)
