"""Whole-simulator properties. Random books run the real pipeline against Postgres.

No production behavior is added here. apply_risk, check_stop_losses,
validate_player_order, create_order, and process_order are the ones already
shipped. Each example rolls back, including its public.users row.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.league.config import RISK_LIMITS, LeagueSettings
from app.league.models import Agent, AgentCycle, Bar, Fill, League, Order, Player, Position, Rationale, Season
from app.league.repo import BarInput, create_order, create_portfolio, get_portfolio, upsert_bars
from app.league.sim.calendar import SessionCalendar
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
from app.league.universe import load_universe
from tests.unit.league.test_league_migration import _config, _engine, _url
from tests.unit.league.test_league_repo import _user_count

_UTC = ZoneInfo("UTC")
_ET = ZoneInfo("America/New_York")
_CASH = Decimal("0.0001")
_STARTING_CASH = Decimal("1000")
_UNIVERSE = load_universe("backend/tests/fixtures/universe_small.csv")
_POOL = ("AAPL", "MSFT", "NVDA", "JPM", "XOM", "SPY")
_LEGAL = frozenset({"pending_approval", "accepted", "rejected", "queued", "filled", "expired", "cancelled"})
_TERMINAL = frozenset({"filled", "rejected", "expired", "cancelled"})
_ACTIVE = frozenset({"accepted", "queued"})

# Example budgets for THIS database test. The decorator reads the active
# Hypothesis profile and applies the matching row. It does not register a
# new global profile, so the other property tests stay at 20 / 300.
_PROFILE_BUDGET = {
    "default": {"max_examples": 3, "derandomize": True},
    "thorough": {"max_examples": 50, "derandomize": False},
}


def _simulator_settings():
    name = settings.get_current_profile_name()
    try:
        budget = _PROFILE_BUDGET[name]
    except KeyError:
        raise RuntimeError(f"no simulator example budget for Hypothesis profile {name!r}") from None
    return settings(
        max_examples=budget["max_examples"],
        derandomize=budget["derandomize"],
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    )


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_ET).astimezone(_UTC)


_EARLY_OPEN = _et(2031, 6, 2, 9, 30)
_EARLY_CLOSE = _et(2031, 6, 2, 13, 0)
_DAY2_OPEN = _et(2031, 6, 3, 9, 30)
_DAY2_CLOSE = _et(2031, 6, 3, 16, 0)
_DAY3_OPEN = _et(2031, 6, 4, 9, 30)
_DAY3_CLOSE = _et(2031, 6, 4, 16, 0)
_START = _et(2031, 6, 2, 9, 0)
_FINAL = _et(2031, 6, 4, 10, 0)
_WAYPOINTS = (
    _et(2031, 6, 2, 10, 0),
    _et(2031, 6, 2, 12, 0),
    _et(2031, 6, 2, 13, 30),
    _et(2031, 6, 2, 20, 0),
    _et(2031, 6, 3, 10, 0),
    _et(2031, 6, 3, 12, 0),
    _et(2031, 6, 3, 15, 0),
    _et(2031, 6, 3, 16, 30),
)
_MANDATORY = (2, 4)
_EXTRA = (0, 1, 3, 5, 6, 7)
_OPENS = (_EARLY_OPEN, _DAY2_OPEN, _DAY3_OPEN)
_FILL_BARS = (
    _et(2031, 6, 2, 10, 5),
    _et(2031, 6, 2, 12, 5),
    _et(2031, 6, 3, 10, 5),
    _et(2031, 6, 3, 12, 5),
    _et(2031, 6, 3, 15, 5),
)
_SIZES = (
    Decimal("0"),
    Decimal("1"),
    Decimal("10"),
    Decimal("50"),
    Decimal("100"),
    Decimal("150"),
)
_CONFIDENCE = (Decimal("0.10"), Decimal("0.50"), Decimal("0.90"))


def _calendar() -> SessionCalendar:
    return SessionCalendar(
        (
            (_EARLY_OPEN, _EARLY_CLOSE),
            (_DAY2_OPEN, _DAY2_CLOSE),
            (_DAY3_OPEN, _DAY3_CLOSE),
        )
    )


def _league_settings(commission: Decimal) -> LeagueSettings:
    return LeagueSettings(
        COMMISSION_USD=commission,
        MIN_ORDER_USD=Decimal("5"),
        SLIPPAGE_BPS=5,
        FILL_TIMEOUT_MINUTES=30,
    )


def _mark(base: int, shock: bool, when: datetime) -> Decimal:
    price = Decimal(base)
    if shock and when >= _DAY2_OPEN:
        price = price / 2
    return price.quantize(_CASH)


def _targets(count: int, extras: tuple[int, ...]) -> tuple[datetime, ...]:
    indexes = tuple(sorted(set(extras) | set(_MANDATORY)))
    assert len(indexes) == count
    return tuple(_WAYPOINTS[index] for index in indexes)


class _Scenario:
    def __init__(
        self,
        level: str,
        commission: Decimal,
        names: tuple[str, ...],
        bases: tuple[int, ...],
        shocks: tuple[bool, ...],
        kept: tuple[tuple[bool, ...], ...],
        targets: tuple[datetime, ...],
        interest: tuple[str, ...],
        exclusion_kind: Optional[str],
        exclusion_value: Optional[str],
        proposals: tuple[tuple[tuple[str, str, Decimal, Decimal], ...], ...],
        players: tuple[Optional[tuple[str, int, Decimal, Decimal]], ...],
    ) -> None:
        self.level = level
        self.commission = commission
        self.names = names
        self.bases = bases
        self.shocks = shocks
        self.kept = kept
        self.targets = targets
        self.interest = interest
        self.exclusion_kind = exclusion_kind
        self.exclusion_value = exclusion_value
        self.proposals = proposals
        self.players = players

    def __repr__(self) -> str:
        return (
            f"Scenario(level={self.level!r}, commission={self.commission}, names={self.names}, "
            f"targets={self.targets!r}, interest={self.interest!r})"
        )


@st.composite
def _scenarios(draw: st.DrawFn) -> _Scenario:
    level = draw(st.sampled_from(("conservative", "balanced", "aggressive")))
    commission = draw(st.sampled_from((Decimal("0"), Decimal("0.01"))))
    names = tuple(draw(st.lists(st.sampled_from(_POOL), min_size=3, max_size=5, unique=True)))
    bases = tuple(draw(st.integers(min_value=12, max_value=40)) for _ in names)
    shocks = tuple(draw(st.booleans()) for _ in names)
    kept = tuple(
        tuple(draw(st.booleans()) for _ in _FILL_BARS)
        for _ in names
    )
    count = draw(st.integers(min_value=2, max_value=8))
    need = count - 2
    extras = tuple(sorted(draw(st.frozensets(st.sampled_from(_EXTRA), min_size=need, max_size=need)))) if need else ()
    interest = tuple(draw(st.lists(st.sampled_from(names), min_size=1, max_size=len(names), unique=True)))
    exclusion_kind = draw(st.sampled_from((None, "ticker", "sector")))
    exclusion_value = None
    if exclusion_kind == "ticker":
        exclusion_value = draw(st.sampled_from(names))
    elif exclusion_kind == "sector":
        exclusion_value = _UNIVERSE.get(draw(st.sampled_from(names))).sector
    proposal = st.tuples(
        st.sampled_from(names + ("ZZZZ",)),
        st.sampled_from(("buy", "sell", "hold")),
        st.sampled_from(_SIZES),
        st.sampled_from(_CONFIDENCE),
    )
    proposals = tuple(
        tuple(draw(st.lists(proposal, min_size=0, max_size=3)))
        for _ in range(count)
    )
    player = st.one_of(
        st.none(),
        st.none(),
        st.none(),
        st.tuples(
            st.sampled_from(("buy", "sell")),
            st.integers(min_value=0, max_value=len(names) - 1),
            st.sampled_from((Decimal("4.99"), Decimal("5"), Decimal("20"), Decimal("5000"))),
            st.sampled_from((Decimal("0.000001"), Decimal("1"), Decimal("1000000"))),
        ),
    )
    players = tuple(draw(player) for _ in range(count))
    return _Scenario(
        level,
        commission,
        names,
        bases,
        shocks,
        kept,
        _targets(count, extras),
        interest,
        exclusion_kind,
        exclusion_value,
        proposals,
        players,
    )


@pytest.fixture(scope="module")
def simulator_engine():
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    yield engine
    after = _user_count(engine)
    engine.dispose()
    assert after == before


def _run(engine, body: Callable[[Session], None]) -> None:
    connection = engine.connect()
    outer = connection.begin()
    session = sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )()
    try:
        body(session)
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _seed(session: Session, level: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = session.execute(
        text(
            """
            INSERT INTO public.users (email, hashed_password, created_at)
            VALUES (:email, 'not-a-login', TIMESTAMPTZ '2031-06-01 14:00:00+00')
            RETURNING id
            """
        ),
        {"email": f"league-sim-{uuid.uuid4()}@local.quantpilot"},
    ).scalar_one()
    player = Player(user_id=user_id, display_name="Sim", avatar_id="avatar")
    session.add(player)
    session.flush()
    league = League(name="Sim", is_public=False, owner_id=player.id)
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        number=1,
        starts_at=_et(2031, 6, 1, 0, 0),
        ends_at=_et(2031, 7, 1, 0, 0),
        status="active",
    )
    session.add(season)
    session.flush()
    agent = Agent(
        player_id=player.id,
        league_id=league.id,
        name="Sim",
        risk_level=level,
        style="momentum",
        mode="autopilot",
        config_version=1,
    )
    session.add(agent)
    session.flush()
    actual = create_portfolio(session, agent.id, season.id, "actual", _STARTING_CASH)
    ghost = create_portfolio(session, agent.id, season.id, "ghost", _STARTING_CASH)
    return agent.id, actual.id, ghost.id


def _bars_for(scenario: _Scenario) -> list[BarInput]:
    bars: list[BarInput] = []
    for index, ticker in enumerate(scenario.names):
        base = scenario.bases[index]
        shock = scenario.shocks[index]
        moments = list(_OPENS)
        for slot, when in enumerate(_FILL_BARS):
            if scenario.kept[index][slot]:
                moments.append(when)
        for when in moments:
            price = _mark(base, shock, when)
            bars.append(BarInput(ticker=ticker, t=when, o=price, h=price, l=price, c=price, v=100, feed="iex"))
    return bars


def _latest_stored(session: Session, tickers: list[str], now: datetime) -> dict[str, Decimal]:
    if not tickers:
        return {}
    rows = session.execute(
        select(Bar.ticker, Bar.t, Bar.c).where(Bar.ticker.in_(tickers), Bar.t <= now).order_by(Bar.t.asc())
    ).all()
    found: dict[str, Decimal] = {}
    for ticker, _when, close in rows:
        found[ticker] = close
    return found


def _latest(bars: list[BarInput], now: datetime) -> dict[str, Decimal]:
    found: dict[str, tuple[datetime, Decimal]] = {}
    for bar in bars:
        if bar.t <= now and (bar.ticker not in found or bar.t > found[bar.ticker][0]):
            found[bar.ticker] = (bar.t, bar.c)
    return {ticker: price for ticker, (_when, price) in found.items()}


def _snapshot(session: Session, portfolio_id: uuid.UUID) -> PortfolioSnapshot:
    view = get_portfolio(session, portfolio_id)
    return PortfolioSnapshot(
        cash=view.portfolio.cash,
        positions=tuple(
            PositionSnapshot(ticker=row.ticker, qty=row.qty, avg_cost=row.avg_cost)
            for row in view.positions
        ),
    )


def _exclusions(scenario: _Scenario) -> Exclusions:
    if scenario.exclusion_kind == "ticker" and scenario.exclusion_value is not None:
        return Exclusions(tickers=frozenset({scenario.exclusion_value}))
    if scenario.exclusion_kind == "sector" and scenario.exclusion_value is not None:
        return Exclusions(sectors=frozenset({scenario.exclusion_value}))
    return Exclusions()


def _buys_today(session: Session, portfolio_id: uuid.UUID, day) -> int:
    orders = session.scalars(select(Order).where(Order.portfolio_id == portfolio_id)).all()
    return sum(
        1
        for order in orders
        if order.source == "agent" and order.side == "buy" and order.game_time.astimezone(_UTC).date() == day
    )


def _agent_rationale(session: Session, agent_id: uuid.UUID, when: datetime, summary: str) -> Rationale:
    cycle = AgentCycle(agent_id=agent_id, trigger="property", started_at=when, status="finished")
    session.add(cycle)
    session.flush()
    rationale = Rationale(
        cycle_id=cycle.id,
        summary=summary,
        confidence=Decimal("0.50"),
        news_ids=["n1"],
        graph_path=["desk"],
        validated=True,
        system=False,
    )
    session.add(rationale)
    session.flush()
    return rationale


def _stop_rationale(session: Session, summary: str) -> Rationale:
    rationale = Rationale(
        cycle_id=None,
        summary=summary or "Stop-loss",
        confidence=Decimal("1.00"),
        news_ids=[],
        graph_path=[],
        validated=True,
        system=True,
    )
    session.add(rationale)
    session.flush()
    return rationale


def _is_system(session: Session, order: Order) -> bool:
    if order.rationale_id is None:
        return False
    rationale = session.get(Rationale, order.rationale_id)
    return bool(rationale and rationale.system)


def _stop_already_pending(session: Session, portfolio_id: uuid.UUID, ticker: str) -> bool:
    orders = session.scalars(
        select(Order).where(
            Order.portfolio_id == portfolio_id,
            Order.ticker == ticker,
            Order.side == "sell",
            Order.status.in_(_ACTIVE),
        )
    ).all()
    return any(_is_system(session, order) for order in orders)


def _insert_order(
    session: Session,
    portfolio_id: uuid.UUID,
    risk_order: RiskOrder,
    when: datetime,
    calendar: SessionCalendar,
    *,
    source: str,
    rationale_id: Optional[uuid.UUID],
) -> Order:
    status = "accepted" if calendar.is_open(when) else "queued"
    if risk_order.side == "buy":
        notional, qty = risk_order.notional_usd, None
    else:
        notional, qty = None, risk_order.qty
    return create_order(
        session,
        portfolio_id=portfolio_id,
        ticker=risk_order.ticker,
        side=risk_order.side,
        source=source,
        status=status,
        game_time=when,
        notional_usd=notional,
        qty=qty,
        rationale_id=rationale_id,
    )


def _equity(snapshot: PortfolioSnapshot, prices: dict[str, Decimal]) -> Decimal:
    return snapshot.cash + sum(position.qty * prices[position.ticker] for position in snapshot.positions)


def _assert_agent_limits(
    level: str,
    snapshot: PortfolioSnapshot,
    prices: dict[str, Decimal],
    orders: tuple[RiskOrder, ...],
    buys_today: int,
    settings: LeagueSettings,
) -> None:
    row = RISK_LIMITS[level]
    equity = _equity(snapshot, prices)
    order_cap = equity * Decimal(int(row["max_single_order_pct"])) / Decimal("100")
    position_cap = equity * Decimal(int(row["max_position_pct"])) / Decimal("100")
    buffer = equity * Decimal(int(row["min_cash_buffer_pct"])) / Decimal("100")
    held = {position.ticker: position for position in snapshot.positions}
    buys = [order for order in orders if order.side == "buy"]
    sells = [order for order in orders if order.side == "sell"]
    spent = sum((order.notional_usd or Decimal("0") for order in buys), Decimal("0"))
    spent += settings.COMMISSION_USD * len(buys)
    if snapshot.cash >= buffer:
        assert snapshot.cash - spent >= buffer
    else:
        assert spent == 0
    assert buys_today + len(buys) <= int(row["max_new_buys_per_day"])
    new_names = {order.ticker for order in buys if order.ticker not in held}
    assert len(held) + len(new_names) <= int(row["max_positions"])
    added: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in buys:
        assert order.notional_usd is not None
        assert order.notional_usd <= order_cap
        assert order.notional_usd >= settings.MIN_ORDER_USD
        added[order.ticker] += order.notional_usd
    for ticker, notional in added.items():
        existing = held[ticker].qty * prices[ticker] if ticker in held else Decimal("0")
        assert existing + notional <= position_cap
    sold: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in sells:
        assert order.qty is not None and order.ticker in held
        assert order.qty <= held[order.ticker].qty
        assert order.qty * prices[order.ticker] >= settings.MIN_ORDER_USD
        sold[order.ticker] += order.qty
    for ticker, qty in sold.items():
        assert qty <= held[ticker].qty


def _assert_stop(level: str, position: PositionSnapshot, price: Decimal, order: RiskOrder) -> None:
    stop = int(RISK_LIMITS[level]["stop_loss_pct"])
    threshold = position.avg_cost * (Decimal(100 + stop) / Decimal("100"))
    assert price <= threshold
    assert order.qty == position.qty


def _assert_player(order: RiskOrder, snapshot: PortfolioSnapshot, prices: dict[str, Decimal], settings: LeagueSettings) -> None:
    if order.side == "buy":
        assert _UNIVERSE.get(order.ticker).ticker == order.ticker
        assert order.notional_usd is not None
        assert order.notional_usd >= settings.MIN_ORDER_USD
        assert order.notional_usd + settings.COMMISSION_USD <= snapshot.cash
        return
    held = {position.ticker: position for position in snapshot.positions}
    assert order.qty is not None and order.ticker in held
    assert order.qty <= held[order.ticker].qty
    assert order.qty * prices[order.ticker] >= settings.MIN_ORDER_USD


def _submit_agent(
    session: Session,
    portfolio_id: uuid.UUID,
    agent_id: uuid.UUID,
    orders: tuple[RiskOrder, ...],
    when: datetime,
    calendar: SessionCalendar,
) -> None:
    for order in orders:
        rationale = _agent_rationale(session, agent_id, when, order.summary or "proposal")
        _insert_order(
            session,
            portfolio_id,
            order,
            when,
            calendar,
            source="agent",
            rationale_id=rationale.id,
        )


def _submit_stops(
    session: Session,
    portfolio_id: uuid.UUID,
    orders: tuple[RiskOrder, ...],
    when: datetime,
    calendar: SessionCalendar,
    level: str,
    snapshot: PortfolioSnapshot,
    prices: dict[str, Decimal],
) -> None:
    held = {position.ticker: position for position in snapshot.positions}
    for order in orders:
        if _stop_already_pending(session, portfolio_id, order.ticker):
            continue
        _assert_stop(level, held[order.ticker], prices[order.ticker], order)
        rationale = _stop_rationale(session, order.summary or "")
        _insert_order(
            session,
            portfolio_id,
            order,
            when,
            calendar,
            source="agent",
            rationale_id=rationale.id,
        )


def _process_open(
    session: Session,
    portfolio_ids: tuple[uuid.UUID, ...],
    clock: ManualClock,
    calendar: SessionCalendar,
    settings: LeagueSettings,
) -> None:
    orders = list(
        session.scalars(
            select(Order).where(
                Order.portfolio_id.in_(portfolio_ids),
                Order.status.in_(_ACTIVE),
            )
        ).all()
    )
    held_now = {
        (row.portfolio_id, row.ticker): row.qty
        for row in session.scalars(select(Position).where(Position.portfolio_id.in_(portfolio_ids))).all()
    }
    annotated = []
    for order in orders:
        size = order.qty if order.qty is not None else order.notional_usd or Decimal("0")
        annotated.append((order.game_time, order.ticker, order.side, int(_is_system(session, order)), size, order.source, order))
    annotated.sort(key=lambda item: item[:6])
    full_sells: dict[tuple[uuid.UUID, str], list[uuid.UUID]] = defaultdict(list)
    for _game_time, ticker, side, _system, _size, _source, order in annotated:
        if side == "sell" and (held_now.get((order.portfolio_id, ticker), Decimal("0")) > 0):
            if order.qty is not None and order.qty >= held_now[(order.portfolio_id, ticker)]:
                full_sells[(order.portfolio_id, ticker)].append(order.id)
    for _game_time, _ticker, _side, _system, _size, _source, order in annotated:
        process_order(session, order.id, clock, calendar, settings)
    session.expire_all()
    for key, order_ids in full_sells.items():
        if len(order_ids) < 2:
            continue
        reloaded = [session.get(Order, order_id) for order_id in order_ids]
        assert reloaded[0] is not None and all(row is not None for row in reloaded)
        if reloaded[0].status != "filled":
            continue
        for later in reloaded[1:]:
            assert later.status == "rejected", (key, later.status, later.reject_reason)
            assert later.reject_reason == "no position"


def _quantize_debit(amount: Decimal) -> Decimal:
    return amount.quantize(_CASH, rounding=ROUND_CEILING)


def _quantize_credit(amount: Decimal) -> Decimal:
    return amount.quantize(_CASH, rounding=ROUND_FLOOR)


def _cash_from_fills(session: Session, portfolio_id: uuid.UUID, commission: Decimal) -> Decimal:
    rows = session.execute(
        select(Order.side, Fill.qty, Fill.price)
        .join(Fill, Fill.order_id == Order.id)
        .where(Order.portfolio_id == portfolio_id)
    ).all()
    cash = _STARTING_CASH
    for side, qty, price in rows:
        if side == "buy":
            cash -= _quantize_debit(qty * price + commission)
        else:
            proceeds = qty * price - commission
            cash += _quantize_credit(proceeds) if proceeds >= 0 else -_quantize_debit(-proceeds)
    return cash


def _assert_books(
    session: Session,
    portfolio_ids: tuple[uuid.UUID, uuid.UUID],
    bars: list[BarInput],
    clock: ManualClock,
    calendar: SessionCalendar,
    commission: Decimal,
    terminal: dict[uuid.UUID, str],
) -> None:
    now = clock.now()
    prices = _latest(bars, now)
    actual_id, ghost_id = portfolio_ids
    books = []
    for portfolio_id in portfolio_ids:
        session.expire_all()
        view = get_portfolio(session, portfolio_id)
        assert view.portfolio.cash >= 0
        for position in view.positions:
            assert position.qty > 0
            assert position.avg_cost == (position.cost_basis / position.qty).quantize(_CASH, rounding=ROUND_HALF_EVEN)
            assert position.ticker in prices
        stored = _latest_stored(session, [position.ticker for position in view.positions], now)
        for position in view.positions:
            assert stored[position.ticker] == prices[position.ticker]
        equity = view.portfolio.cash + sum(
            (position.qty * stored[position.ticker] for position in view.positions),
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
                        WHERE ticker = pos.ticker AND t <= :now
                        ORDER BY t DESC
                        LIMIT 1
                    ) latest ON true
                    WHERE pos.portfolio_id = p.id
                ), 0)
                FROM league.portfolios p
                WHERE p.id = :portfolio_id
                """
            ),
            {"now": now, "portfolio_id": portfolio_id},
        )
        assert equity == sql_equity
        assert view.portfolio.cash == _cash_from_fills(session, portfolio_id, commission)
        fills = session.execute(
            select(Order.ticker, Order.side, Fill.qty)
            .join(Fill, Fill.order_id == Order.id)
            .where(Order.portfolio_id == portfolio_id)
        ).all()
        signed: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for ticker, side, qty in fills:
            signed[ticker] += qty if side == "buy" else -qty
        held = {position.ticker: position.qty for position in view.positions}
        for ticker, qty in signed.items():
            if qty == 0:
                assert ticker not in held
            else:
                assert held.get(ticker) == qty
        assert set(held) == {ticker for ticker, qty in signed.items() if qty != 0}
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
            assert order.status in _LEGAL
            count = int(fill_counts.get(order.id, 0))
            if order.status == "filled":
                assert count == 1
            else:
                assert count == 0
            previous = terminal.get(order.id)
            if previous in _TERMINAL:
                assert order.status == previous
            if order.status in _TERMINAL:
                terminal[order.id] = order.status
            fill = session.scalar(select(Fill).where(Fill.order_id == order.id))
            if fill is not None:
                assert fill.bar_time > order.game_time
                assert calendar.is_open(fill.bar_time)
                assert fill.bar_time <= now
        books.append(
            (
                view.portfolio.cash,
                tuple((row.ticker, row.qty, row.avg_cost, row.cost_basis) for row in view.positions),
                tuple(
                    sorted(
                        session.execute(
                            select(Order.ticker, Order.side, Fill.qty, Fill.price, Fill.bar_time)
                            .join(Fill, Fill.order_id == Order.id)
                            .where(Order.portfolio_id == portfolio_id)
                        ).all()
                    )
                ),
            )
        )
    ghost_orders = session.scalars(select(Order).where(Order.portfolio_id == ghost_id, Order.source == "player")).all()
    assert ghost_orders == []
    player_orders = session.scalars(select(Order).where(Order.portfolio_id == actual_id, Order.source == "player")).all()
    if not player_orders:
        assert books[0] == books[1]


def _advance_across(clock: ManualClock, target: datetime) -> None:
    assert target >= clock.now()
    clock.advance(target - clock.now())


def _play(session: Session, scenario: _Scenario) -> None:
    moments = (_START, *scenario.targets)
    assert any(left < _EARLY_CLOSE <= right for left, right in zip(moments, moments[1:]))
    assert any(left < _DAY2_OPEN <= right for left, right in zip(moments, moments[1:]))
    settings = _league_settings(scenario.commission)
    calendar = _calendar()
    agent_id, actual_id, ghost_id = _seed(session, scenario.level)
    portfolio_ids = (actual_id, ghost_id)
    window = session.scalar(
        select(func.count())
        .select_from(Bar)
        .where(Bar.ticker.in_(scenario.names), Bar.t >= _START, Bar.t <= _FINAL)
    )
    assert window == 0, "scenario window already has bars; the example would not own its prices"
    bars = _bars_for(scenario)
    assert upsert_bars(session, bars) == len(bars)
    clock = ManualClock(_START)
    terminal: dict[uuid.UUID, str] = {}
    exclusions = _exclusions(scenario)
    for index, target in enumerate(scenario.targets):
        when = clock.now()
        prices = _latest(bars, when)
        proposals = [
            Proposal(ticker=ticker, action=action, size_pct_of_equity=size, confidence=confidence)
            for ticker, action, size, confidence in scenario.proposals[index]
        ]
        for portfolio_id in portfolio_ids:
            snapshot = _snapshot(session, portfolio_id)
            if any(position.ticker not in prices for position in snapshot.positions):
                raise AssertionError("a held ticker has no price at or before the clock")
            today = _buys_today(session, portfolio_id, when.astimezone(_UTC).date())
            orders, _dropped = apply_risk(
                proposals,
                snapshot,
                prices,
                scenario.level,
                _UNIVERSE,
                scenario.interest,
                exclusions,
                today,
                settings,
            )
            _assert_agent_limits(scenario.level, snapshot, prices, orders, today, settings)
            _submit_agent(session, portfolio_id, agent_id, orders, when, calendar)
        _advance_across(clock, target)
        _process_open(session, portfolio_ids, clock, calendar, settings)
        prices = _latest(bars, clock.now())
        for portfolio_id in portfolio_ids:
            snapshot = _snapshot(session, portfolio_id)
            if any(position.ticker not in prices for position in snapshot.positions):
                raise AssertionError("a held ticker has no price at or before the clock")
            stops = check_stop_losses(snapshot.positions, prices, scenario.level)
            _submit_stops(session, portfolio_id, stops, clock.now(), calendar, scenario.level, snapshot, prices)
        player = scenario.players[index]
        if player is not None:
            side, ticker_index, notional, qty = player
            ticker = scenario.names[ticker_index]
            if ticker in prices:
                snapshot = _snapshot(session, actual_id)
                order = PlayerOrder(
                    ticker=ticker,
                    side=side,
                    notional_usd=notional if side == "buy" else None,
                    qty=qty if side == "sell" else None,
                )
                decided = validate_player_order(order, snapshot, prices, _UNIVERSE, settings)
                if isinstance(decided, RiskOrder):
                    _assert_player(decided, snapshot, prices, settings)
                    _insert_order(
                        session,
                        actual_id,
                        decided,
                        clock.now(),
                        calendar,
                        source="player",
                        rationale_id=None,
                    )
                else:
                    assert isinstance(decided, Dropped)
        _assert_books(session, portfolio_ids, bars, clock, calendar, scenario.commission, terminal)
    if clock.now() < _FINAL:
        _advance_across(clock, _FINAL)
    _process_open(session, portfolio_ids, clock, calendar, settings)
    _assert_books(session, portfolio_ids, bars, clock, calendar, scenario.commission, terminal)


@_simulator_settings()
@given(scenario=_scenarios())
def test_random_simulator_holds_every_invariant(simulator_engine, scenario: _Scenario) -> None:
    _run(simulator_engine, lambda session: _play(session, scenario))


def test_order_queued_after_the_close_fills_at_the_next_open(simulator_engine) -> None:
    def body(session: Session) -> None:
        settings = _league_settings(Decimal("0"))
        calendar = _calendar()
        _agent_id, actual_id, _ghost_id = _seed(session, "balanced")
        price = Decimal("20")
        morning = _et(2031, 6, 2, 10, 0)
        nxt = _DAY2_OPEN
        bars = [
            BarInput(ticker="AAPL", t=morning, o=price, h=price, l=price, c=price, v=100, feed="iex"),
            BarInput(ticker="AAPL", t=nxt, o=Decimal("21"), h=Decimal("21"), l=Decimal("21"), c=Decimal("21"), v=100, feed="iex"),
        ]
        assert upsert_bars(session, bars) == 2
        clock = ManualClock(_et(2031, 6, 2, 13, 30))
        assert not calendar.is_open(clock.now())
        snapshot = _snapshot(session, actual_id)
        decided = validate_player_order(
            PlayerOrder(ticker="AAPL", side="buy", notional_usd=Decimal("20")),
            snapshot,
            _latest(bars, clock.now()),
            _UNIVERSE,
            settings,
        )
        assert isinstance(decided, RiskOrder)
        order = _insert_order(
            session,
            actual_id,
            decided,
            clock.now(),
            calendar,
            source="player",
            rationale_id=None,
        )
        assert order.status == "queued"
        _advance_across(clock, _et(2031, 6, 3, 9, 31))
        process_order(session, order.id, clock, calendar, settings)
        session.expire_all()
        stored = session.get(Order, order.id)
        fill = session.scalar(select(Fill).where(Fill.order_id == order.id))
        assert stored is not None and stored.status == "filled"
        assert fill is not None and fill.bar_time == nxt
        assert calendar.is_open(fill.bar_time)
        assert fill.bar_time > stored.game_time

    _run(simulator_engine, body)


def test_second_of_two_pending_sells_is_rejected(simulator_engine) -> None:
    """A full agent sell and a stop-loss sell are both pending. The second rejects."""

    def body(session: Session) -> None:
        settings = _league_settings(Decimal("0"))
        calendar = _calendar()
        agent_id, actual_id, _ghost_id = _seed(session, "balanced")
        first = _et(2031, 6, 2, 10, 0)
        fill_at = _et(2031, 6, 2, 10, 30)
        crash = _DAY2_OPEN
        later = _et(2031, 6, 3, 9, 45)
        bars = []
        for when, price in (
            (first, Decimal("50")),
            (fill_at, Decimal("50")),
            (crash, Decimal("10")),
            (later, Decimal("10")),
        ):
            bars.append(BarInput(ticker="AAPL", t=when, o=price, h=price, l=price, c=price, v=100, feed="iex"))
        assert upsert_bars(session, bars) == 4
        clock = ManualClock(first)
        snapshot = _snapshot(session, actual_id)
        decided = validate_player_order(
            PlayerOrder(ticker="AAPL", side="buy", notional_usd=Decimal("100")),
            snapshot,
            _latest(bars, clock.now()),
            _UNIVERSE,
            settings,
        )
        assert isinstance(decided, RiskOrder)
        buy = _insert_order(session, actual_id, decided, clock.now(), calendar, source="player", rationale_id=None)
        _advance_across(clock, fill_at)
        process_order(session, buy.id, clock, calendar, settings)
        _advance_across(clock, crash)
        prices = _latest(bars, clock.now())
        snapshot = _snapshot(session, actual_id)
        assert len(snapshot.positions) == 1
        orders, dropped = apply_risk(
            [Proposal(ticker="AAPL", action="sell", size_pct_of_equity=Decimal("100"), confidence=Decimal("0.90"))],
            snapshot,
            prices,
            "balanced",
            _UNIVERSE,
            ("AAPL",),
            Exclusions(),
            0,
            settings,
        )
        assert dropped == ()
        assert len(orders) == 1 and orders[0].side == "sell"
        rationale = _agent_rationale(session, agent_id, clock.now(), "sell")
        agent_sell = _insert_order(
            session, actual_id, orders[0], clock.now(), calendar, source="agent", rationale_id=rationale.id
        )
        stops = check_stop_losses(snapshot.positions, prices, "balanced")
        assert len(stops) == 1
        _assert_stop("balanced", snapshot.positions[0], prices["AAPL"], stops[0])
        stop_row = _stop_rationale(session, stops[0].summary or "")
        stop = _insert_order(
            session, actual_id, stops[0], clock.now(), calendar, source="agent", rationale_id=stop_row.id
        )
        assert agent_sell.status in _ACTIVE and stop.status in _ACTIVE
        _advance_across(clock, later)
        process_order(session, agent_sell.id, clock, calendar, settings)
        process_order(session, stop.id, clock, calendar, settings)
        session.expire_all()
        agent_stored = session.get(Order, agent_sell.id)
        stop_stored = session.get(Order, stop.id)
        assert agent_stored is not None and agent_stored.status == "filled"
        assert stop_stored is not None and stop_stored.status == "rejected"
        assert stop_stored.reject_reason == "no position"
        view = get_portfolio(session, actual_id)
        assert view.positions == ()
        assert view.portfolio.cash >= 0

    _run(simulator_engine, body)
