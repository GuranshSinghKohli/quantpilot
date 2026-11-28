"""Pure risk checks. No session and no wall clock.

Limits are read from RISK_LIMITS. MIN_ORDER_USD and COMMISSION_USD come from
LeagueSettings. Buys are sized against current cash only: a sell in this call
does not add proceeds and does not free a position slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from app.league.config import RISK_LIMITS, LeagueSettings
from app.league.universe import Universe, UniverseTickerNotFound

_CASH = Decimal("0.0001")
_QTY = Decimal("0.000001")
_HUNDRED = Decimal("100")


class MissingPrice(Exception):
    """A price the check needs is missing or not positive. No price is invented."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"missing or non-positive price: {ticker}")


@dataclass(frozen=True)
class PositionSnapshot:
    ticker: str
    qty: Decimal
    avg_cost: Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Cash and holdings the risk engine can see. Not the ORM portfolio view."""

    cash: Decimal
    positions: tuple[PositionSnapshot, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))


@dataclass(frozen=True)
class Proposal:
    ticker: str
    action: str
    size_pct_of_equity: Decimal
    confidence: Decimal


@dataclass(frozen=True)
class Exclusions:
    tickers: frozenset[str] = field(default_factory=frozenset)
    sectors: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tickers", frozenset(self.tickers))
        object.__setattr__(self, "sectors", frozenset(self.sectors))


@dataclass(frozen=True)
class RiskOrder:
    ticker: str
    side: str
    qty: Decimal | None = None
    notional_usd: Decimal | None = None
    system: bool = False
    summary: str | None = None
    clip_reason: str | None = None
    validated: bool = False
    graph_path: tuple[object, ...] = ()
    news_ids: tuple[object, ...] = ()


@dataclass(frozen=True)
class Dropped:
    ticker: str
    action: str
    reason: str


@dataclass(frozen=True)
class PlayerOrder:
    """A person-sized order. Buys carry notional_usd. Sells carry qty."""

    ticker: str
    side: str
    notional_usd: Decimal | None = None
    qty: Decimal | None = None


@dataclass(frozen=True)
class _Caps:
    order_pct: int
    position_pct: int
    max_positions: int
    max_buys: int
    stop_pct: int
    etfs_allowed: bool
    order_cap: Decimal
    position_cap: Decimal
    buffer: Decimal


def apply_risk(
    proposals: Sequence[Proposal],
    portfolio: PortfolioSnapshot,
    prices: Mapping[str, Decimal],
    risk_level: str,
    universe: Universe,
    interest_tickers: Iterable[str],
    exclusions: Exclusions,
    buys_today: int,
    settings: LeagueSettings,
) -> tuple[tuple[RiskOrder, ...], tuple[Dropped, ...]]:
    """Return the orders that fit, and every other proposal with a reason.

    Sells run first, in input order. Buys run by confidence descending.
    Nothing is resized by adding the cash a sell might later produce.
    """
    minimum = _setting_money(settings.MIN_ORDER_USD, "MIN_ORDER_USD")
    commission = _setting_money(settings.COMMISSION_USD, "COMMISSION_USD")
    today = _count(buys_today, "buys_today")
    held = _holdings(portfolio)
    quoted = _prices(prices)
    _require_prices(held, quoted)
    _reject_bad_quotes(proposals, quoted)

    for proposal in proposals:
        if proposal.action == "buy":
            _confidence(proposal)
    equity = portfolio.cash + sum(position.qty * quoted[position.ticker] for position in held.values())
    caps = _caps(risk_level, equity)
    interest = set(interest_tickers)
    sell_names = {proposal.ticker for proposal in proposals if proposal.action == "sell"}
    open_names = set(held)
    remaining = {ticker: position.qty for ticker, position in held.items()}
    committed: dict[str, Decimal] = {}
    spent = Decimal("0")
    accepted = today
    slots = len(open_names)

    dropped: list[Dropped] = []
    sell_orders: list[RiskOrder] = []
    buy_orders: list[RiskOrder] = []

    for proposal in proposals:
        if proposal.action != "sell":
            continue
        reason = _sell_gate(proposal, held)
        if reason is not None:
            dropped.append(Dropped(proposal.ticker, proposal.action, reason))
            continue
        order, reason = _size_sell(proposal, held, remaining, quoted, minimum)
        if order is None:
            dropped.append(Dropped(proposal.ticker, proposal.action, reason or "invalid size"))
            continue
        remaining[proposal.ticker] = remaining[proposal.ticker] - (order.qty or Decimal("0"))
        sell_orders.append(order)

    buy_indexes = [index for index, proposal in enumerate(proposals) if proposal.action == "buy"]
    buy_indexes.sort(key=lambda index: (-_confidence(proposals[index]), proposals[index].ticker, index))
    for index in buy_indexes:
        proposal = proposals[index]
        reason = _buy_gate(proposal, universe, interest, exclusions, caps.etfs_allowed, sell_names)
        if reason is not None:
            dropped.append(Dropped(proposal.ticker, proposal.action, reason))
            continue
        final, clip = _clip_buy(
            proposal,
            equity,
            caps,
            held,
            quoted,
            committed,
            portfolio.cash,
            spent,
            commission,
        )
        if proposal.ticker not in open_names and slots >= caps.max_positions:
            dropped.append(Dropped(proposal.ticker, proposal.action, "max positions"))
            continue
        if accepted >= caps.max_buys:
            dropped.append(Dropped(proposal.ticker, proposal.action, "max buys per day"))
            continue
        if final <= 0 or final < minimum:
            dropped.append(Dropped(proposal.ticker, proposal.action, clip or _minimum_reason(minimum)))
            continue
        buy_orders.append(
            RiskOrder(
                ticker=proposal.ticker,
                side="buy",
                notional_usd=final,
                clip_reason=clip,
            )
        )
        committed[proposal.ticker] = committed.get(proposal.ticker, Decimal("0")) + final
        spent += final + commission
        accepted += 1
        if proposal.ticker not in open_names:
            open_names.add(proposal.ticker)
            slots += 1

    for proposal in proposals:
        if proposal.action in {"buy", "sell"}:
            continue
        dropped.append(Dropped(proposal.ticker, proposal.action, "invalid size"))

    return tuple(sell_orders + buy_orders), tuple(dropped)


def check_stop_losses(
    positions: Sequence[PositionSnapshot],
    prices: Mapping[str, Decimal],
    risk_level: str,
) -> tuple[RiskOrder, ...]:
    """Sell the full quantity of every position at or below its stop.

    The $5 minimum does not apply. This does not insert a rationale and it
    does not look at open orders. The connector must not submit a second
    stop-loss sell while one is already pending for that ticker (section 9).
    """
    row = _row(risk_level)
    stop = int(row["stop_loss_pct"])
    quoted = _prices(prices)
    orders: list[RiskOrder] = []
    seen: set[str] = set()
    for position in positions:
        if position.ticker in seen:
            raise ValueError(f"duplicate position: {position.ticker}")
        seen.add(position.ticker)
        if not isinstance(position.qty, Decimal) or position.qty <= 0:
            raise ValueError(f"position quantity must be positive: {position.ticker}")
        if not isinstance(position.avg_cost, Decimal) or position.avg_cost <= 0:
            raise ValueError(f"average cost must be positive: {position.ticker}")
        price = _required_price(quoted, position.ticker)
        threshold = position.avg_cost * (Decimal(100 + stop) / _HUNDRED)
        if price > threshold:
            continue
        orders.append(
            RiskOrder(
                ticker=position.ticker,
                side="sell",
                qty=position.qty,
                notional_usd=position.qty * price,
                system=True,
                summary=f"Stop-loss at {stop}%",
                validated=True,
            )
        )
    return tuple(orders)


def validate_player_order(
    order: PlayerOrder,
    portfolio: PortfolioSnapshot,
    prices: Mapping[str, Decimal],
    universe: Universe,
    settings: LeagueSettings,
) -> RiskOrder | Dropped:
    """Accept a player order, or drop it.

    Buys must be in the universe. Sells of a held ticker are allowed after it
    has left the universe. Cash, no-shorting, and the $5 minimum apply.
    Position limits, the buffer, exclusions, and interests do not.
    """
    minimum = _setting_money(settings.MIN_ORDER_USD, "MIN_ORDER_USD")
    commission = _setting_money(settings.COMMISSION_USD, "COMMISSION_USD")
    if not isinstance(portfolio.cash, Decimal):
        raise ValueError("cash must be a Decimal")
    quoted = _prices(prices)
    price = _required_price(quoted, order.ticker)
    held = _holdings(portfolio)
    if order.side == "buy":
        return _player_buy(order, portfolio.cash, universe, minimum, commission)
    if order.side == "sell":
        return _player_sell(order, price, held, minimum)
    return Dropped(order.ticker, order.side, "invalid size")


def _player_buy(
    order: PlayerOrder,
    cash: Decimal,
    universe: Universe,
    minimum: Decimal,
    commission: Decimal,
) -> RiskOrder | Dropped:
    notional = order.notional_usd
    if not isinstance(notional, Decimal) or notional <= 0:
        return Dropped(order.ticker, order.side, "invalid size")
    if _listing(universe, order.ticker) is None:
        return Dropped(order.ticker, order.side, "not in universe")
    if notional < minimum:
        return Dropped(order.ticker, order.side, _minimum_reason(minimum))
    if notional + commission > cash:
        return Dropped(order.ticker, order.side, "insufficient cash")
    return RiskOrder(ticker=order.ticker, side="buy", notional_usd=notional)


def _player_sell(
    order: PlayerOrder,
    price: Decimal,
    held: Mapping[str, PositionSnapshot],
    minimum: Decimal,
) -> RiskOrder | Dropped:
    qty = order.qty
    if not isinstance(qty, Decimal) or qty <= 0:
        return Dropped(order.ticker, order.side, "invalid size")
    position = held.get(order.ticker)
    if position is None:
        return Dropped(order.ticker, order.side, "not held")
    if qty > position.qty:
        return Dropped(order.ticker, order.side, "exceeds held")
    if qty * price < minimum:
        return Dropped(order.ticker, order.side, _minimum_reason(minimum))
    return RiskOrder(ticker=order.ticker, side="sell", qty=qty, notional_usd=qty * price)


def _sell_gate(proposal: Proposal, held: Mapping[str, PositionSnapshot]) -> str | None:
    size = _number(proposal.size_pct_of_equity)
    if size is None or size <= 0 or size > _HUNDRED:
        return "invalid size"
    if proposal.ticker not in held:
        return "not held"
    return None


def _size_sell(
    proposal: Proposal,
    held: Mapping[str, PositionSnapshot],
    remaining: Mapping[str, Decimal],
    quoted: Mapping[str, Decimal],
    minimum: Decimal,
) -> tuple[RiskOrder | None, str | None]:
    size = _number(proposal.size_pct_of_equity)
    if size is None:
        return None, "invalid size"
    position = held[proposal.ticker]
    raw = _floor_qty(position.qty * size / _HUNDRED)
    if raw <= 0:
        return None, "size rounds to zero"
    left = remaining[proposal.ticker]
    qty = raw if raw <= left else _floor_qty(left)
    if qty <= 0:
        return None, "exceeds held"
    clip = None if qty == raw else "clipped: exceeds held"
    notional = qty * quoted[proposal.ticker]
    if notional < minimum:
        return None, clip or _minimum_reason(minimum)
    return (
        RiskOrder(
            ticker=proposal.ticker,
            side="sell",
            qty=qty,
            notional_usd=notional,
            clip_reason=clip,
        ),
        None,
    )


def _buy_gate(
    proposal: Proposal,
    universe: Universe,
    interest: set[str],
    exclusions: Exclusions,
    etfs_allowed: bool,
    sell_names: set[str],
) -> str | None:
    size = _number(proposal.size_pct_of_equity)
    if size is None or size <= 0:
        return "invalid size"
    if proposal.ticker in exclusions.tickers:
        return "excluded"
    listing = _listing(universe, proposal.ticker)
    if listing is None:
        return "not in universe"
    if listing.sector in exclusions.sectors:
        return "excluded"
    if proposal.ticker not in interest:
        return "outside interests"
    if listing.sector == "ETF" and not etfs_allowed:
        return "etf not allowed"
    if proposal.ticker in sell_names:
        return "conflicting proposals"
    return None


def _clip_buy(
    proposal: Proposal,
    equity: Decimal,
    caps: _Caps,
    held: Mapping[str, PositionSnapshot],
    quoted: Mapping[str, Decimal],
    committed: Mapping[str, Decimal],
    cash: Decimal,
    spent: Decimal,
    commission: Decimal,
) -> tuple[Decimal, str | None]:
    size = _number(proposal.size_pct_of_equity) or Decimal("0")
    final = _floor_cash(equity * size / _HUNDRED)
    clip: str | None = None
    final, clip = _tighten(final, caps.order_cap, f"clipped: max order {caps.order_pct}%", clip)
    existing = Decimal("0")
    position = held.get(proposal.ticker)
    if position is not None:
        existing = position.qty * quoted[proposal.ticker]
    room = caps.position_cap - existing - committed.get(proposal.ticker, Decimal("0"))
    final, clip = _tighten(final, room, f"clipped: max position {caps.position_pct}%", clip)
    cash_room = cash - caps.buffer - spent - commission
    return _tighten(final, cash_room, "clipped: cash buffer", clip)


def _tighten(
    final: Decimal,
    limit: Decimal,
    reason: str,
    current: str | None,
) -> tuple[Decimal, str | None]:
    if final > limit:
        clipped = _floor_cash(limit) if limit > 0 else Decimal("0")
        return clipped, reason
    return final, current


def _caps(risk_level: str, equity: Decimal) -> _Caps:
    row = _row(risk_level)
    order_pct = int(row["max_single_order_pct"])
    position_pct = int(row["max_position_pct"])
    buffer_pct = int(row["min_cash_buffer_pct"])
    return _Caps(
        order_pct=order_pct,
        position_pct=position_pct,
        max_positions=int(row["max_positions"]),
        max_buys=int(row["max_new_buys_per_day"]),
        stop_pct=int(row["stop_loss_pct"]),
        etfs_allowed=bool(row["etfs_allowed"]),
        order_cap=equity * Decimal(order_pct) / _HUNDRED,
        position_cap=equity * Decimal(position_pct) / _HUNDRED,
        buffer=equity * Decimal(buffer_pct) / _HUNDRED,
    )


def _row(risk_level: str) -> Mapping[str, int | bool]:
    try:
        return RISK_LIMITS[risk_level]
    except KeyError:
        raise ValueError(f"unknown risk level: {risk_level!r}") from None


def _holdings(portfolio: PortfolioSnapshot) -> dict[str, PositionSnapshot]:
    if not isinstance(portfolio.cash, Decimal):
        raise ValueError("cash must be a Decimal")
    held: dict[str, PositionSnapshot] = {}
    for position in portfolio.positions:
        if position.ticker in held:
            raise ValueError(f"duplicate position: {position.ticker}")
        if not isinstance(position.qty, Decimal) or position.qty <= 0:
            raise ValueError(f"position quantity must be positive: {position.ticker}")
        if not isinstance(position.avg_cost, Decimal) or position.avg_cost <= 0:
            raise ValueError(f"average cost must be positive: {position.ticker}")
        held[position.ticker] = position
    return held


def _prices(prices: Mapping[str, Decimal]) -> dict[str, Decimal]:
    quoted: dict[str, Decimal] = {}
    for ticker, price in prices.items():
        if isinstance(price, bool) or not isinstance(price, Decimal):
            raise ValueError(f"price must be a Decimal: {ticker}")
        quoted[ticker] = price
    return quoted


def _require_prices(held: Mapping[str, PositionSnapshot], quoted: Mapping[str, Decimal]) -> None:
    for ticker in held:
        _required_price(quoted, ticker)


def _reject_bad_quotes(proposals: Sequence[Proposal], quoted: Mapping[str, Decimal]) -> None:
    for proposal in proposals:
        if proposal.ticker in quoted and quoted[proposal.ticker] <= 0:
            raise MissingPrice(proposal.ticker)


def _required_price(quoted: Mapping[str, Decimal], ticker: str) -> Decimal:
    if ticker not in quoted or quoted[ticker] <= 0:
        raise MissingPrice(ticker)
    return quoted[ticker]


def _listing(universe: Universe, ticker: str):
    try:
        return universe.get(ticker)
    except UniverseTickerNotFound:
        return None


def _confidence(proposal: Proposal) -> Decimal:
    number = _number(proposal.confidence)
    if number is None:
        raise ValueError("confidence must be a Decimal")
    return number


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _setting_money(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal) or value < 0:
        raise ValueError(f"{label} must be a Decimal >= 0")
    return value


def _count(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _floor_cash(value: Decimal) -> Decimal:
    return value.quantize(_CASH, rounding=ROUND_DOWN)


def _floor_qty(value: Decimal) -> Decimal:
    return value.quantize(_QTY, rounding=ROUND_DOWN)


def _minimum_reason(minimum: Decimal) -> str:
    if minimum == Decimal("5"):
        return "below $5 minimum"
    shown = format(minimum.normalize(), "f")
    return f"below ${shown} minimum"
