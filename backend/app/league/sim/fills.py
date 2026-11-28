"""Pure fill decision. No session and no wall clock.

A bar at exactly FILL_TIMEOUT_MINUTES of trading time after T still fills.
Expiry starts only when the trading time from T to now is greater than that
limit and no eligible bar exists. A bar at the next open is eligible. Every
fill bar is inside a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Callable, Optional, Union

from app.league.repo.portfolios import quantize_fill
from app.league.sim.calendar import CalendarUnavailable, MarketCalendar
from app.league.sim.clock import _as_utc

_QTY = Decimal("0.000001")
_BPS = Decimal("10000")


@dataclass(frozen=True)
class FillOrder:
    side: str
    notional_usd: Optional[Decimal] = None
    qty: Optional[Decimal] = None
    fraction: Optional[Decimal] = None


@dataclass(frozen=True)
class BarQuote:
    t: datetime
    open: Decimal


@dataclass(frozen=True)
class Fill:
    price: Decimal
    qty: Decimal
    bar_time: datetime


@dataclass(frozen=True)
class Wait:
    """No eligible bar yet, and the trading-minute timeout has not passed."""


@dataclass(frozen=True)
class Expire:
    reason: str


Decision = Union[Fill, Wait, Expire]
BarLookup = Callable[..., Optional[BarQuote]]


def decide_fill(
    order: FillOrder,
    accepted_at: datetime,
    held_qty: Decimal,
    slippage_bps: int,
    lookup: BarLookup,
    now: datetime,
    calendar: MarketCalendar,
    timeout_minutes: int,
) -> Decision:
    """Return the fill, a wait, or an expiry. Raises CalendarUnavailable."""
    if isinstance(slippage_bps, bool) or not isinstance(slippage_bps, int) or slippage_bps < 0:
        raise ValueError(f"slippage_bps must be a non-negative integer: {slippage_bps!r}")
    if isinstance(timeout_minutes, bool) or not isinstance(timeout_minutes, int) or timeout_minutes < 0:
        raise ValueError(f"timeout minutes must be a non-negative integer: {timeout_minutes!r}")
    accepted = _as_utc(accepted_at)
    clock = _as_utc(now)
    if clock < accepted:
        return Wait()

    if calendar.is_open(accepted):
        after = accepted
        inclusive = False
        next_open_at = None
    else:
        opened = calendar.next_open(accepted)
        if opened is None:
            raise CalendarUnavailable(accepted)
        next_open_at = _as_utc(opened)
        after = next_open_at
        inclusive = True

    previous: Optional[datetime] = None
    while True:
        bar = lookup(after, clock, inclusive=inclusive)
        if bar is None:
            break
        bar_time = _as_utc(bar.t)
        if previous is not None and bar_time <= previous:
            raise RuntimeError("bar lookup did not advance")
        previous = bar_time
        if bar_time > clock:
            break
        too_early = bar_time <= accepted if next_open_at is None else bar_time < next_open_at
        if too_early or not calendar.is_open(bar_time):
            after = bar_time
            inclusive = False
            continue
        if calendar.trading_minutes(accepted, bar_time) > timeout_minutes:
            return Expire("timeout")
        return _sized_fill(order, held_qty, slippage_bps, bar.open, bar_time)
    if calendar.trading_minutes(accepted, clock) > timeout_minutes:
        return Expire("timeout")
    return Wait()


def _sized_fill(
    order: FillOrder,
    held_qty: Decimal,
    slippage_bps: int,
    open_price: Decimal,
    bar_time: datetime,
) -> Decision:
    price = _slipped_price(order.side, open_price, slippage_bps)
    if price <= 0:
        return Expire("price rounds to zero")
    qty = _quantity(order, held_qty, price)
    if isinstance(qty, Expire):
        return qty
    if qty <= 0:
        return Expire("size rounds to zero")
    return Fill(price=price, qty=qty, bar_time=bar_time)


def _slipped_price(side: str, open_price: Decimal, slippage_bps: int) -> Decimal:
    if side not in {"buy", "sell"}:
        raise ValueError(f"side must be buy or sell: {side!r}")
    bps = Decimal(slippage_bps) / _BPS
    raw = open_price * (1 + bps) if side == "buy" else open_price * (1 - bps)
    _qty, price = quantize_fill(side, Decimal("1"), raw)
    return price


def _quantity(order: FillOrder, held_qty: Decimal, price: Decimal) -> Decimal | Expire:
    if order.fraction is not None:
        if order.fraction <= 0 or order.fraction > 1:
            return Expire("bad fraction")
        qty = _floor_qty(held_qty * order.fraction)
    elif order.side == "buy" and order.notional_usd is not None:
        qty = _floor_qty(order.notional_usd / price)
        while qty > 0 and qty * price > order.notional_usd:
            qty -= _QTY
    elif order.qty is not None:
        qty = _floor_qty(order.qty)
    else:
        return Expire("unsized")
    if order.side == "sell" and qty > held_qty:
        qty = held_qty
    return qty


def _floor_qty(value: Decimal) -> Decimal:
    return value.quantize(_QTY, rounding=ROUND_DOWN)
