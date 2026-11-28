"""Section 9 risk engine. Pure checks: no database and no clock."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.league.config import RISK_LIMITS, LeagueSettings
from app.league.sim.risk import (
    Dropped,
    Exclusions,
    MissingPrice,
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

UNIVERSE = load_universe("backend/tests/fixtures/universe_small.csv")
INTEREST = tuple(listing.ticker for listing in UNIVERSE.listings)
NAMES = tuple(ticker for ticker in INTEREST if ticker not in {"SPY", "QQQ", "XLK", "SMH"})
LEVELS = ("conservative", "balanced", "aggressive")
_HUNDRED = Decimal("100")


def _settings(**overrides: Decimal) -> LeagueSettings:
    values: dict[str, Decimal] = {
        "COMMISSION_USD": Decimal("0"),
        "MIN_ORDER_USD": Decimal("5"),
    }
    values.update(overrides)
    return LeagueSettings(**values)


def _proposal(ticker: str, action: str, size: Decimal | str, confidence: Decimal | str = "0.5") -> Proposal:
    return Proposal(ticker, action, Decimal(str(size)), Decimal(str(confidence)))


def _apply(
    proposals: list[Proposal],
    portfolio: PortfolioSnapshot,
    prices: dict[str, Decimal],
    level: str = "balanced",
    interest: tuple[str, ...] | list[str] = INTEREST,
    exclusions: Exclusions | None = None,
    buys_today: int = 0,
    settings: LeagueSettings | None = None,
) -> tuple[tuple[RiskOrder, ...], tuple[Dropped, ...]]:
    return apply_risk(
        proposals,
        portfolio,
        prices,
        level,
        UNIVERSE,
        interest,
        exclusions or Exclusions(),
        buys_today,
        settings or _settings(),
    )


def _equity(portfolio: PortfolioSnapshot, prices: dict[str, Decimal]) -> Decimal:
    return portfolio.cash + sum(position.qty * prices[position.ticker] for position in portfolio.positions)


@pytest.mark.parametrize("level", LEVELS)
def test_single_order_cap_allows_the_limit_and_clips_one_cent(level: str) -> None:
    pct = int(RISK_LIMITS[level]["max_single_order_pct"])
    cap = Decimal("1000") * Decimal(pct) / _HUNDRED
    book = PortfolioSnapshot(Decimal("1000"))
    prices = {"AAPL": Decimal("10")}
    at_limit, dropped = _apply([_proposal("AAPL", "buy", pct)], book, prices, level=level)
    assert dropped == ()
    assert at_limit[0].notional_usd == cap
    assert at_limit[0].clip_reason is None

    over_pct = (cap + Decimal("0.01")) / Decimal("10")
    over, dropped = _apply([_proposal("AAPL", "buy", over_pct)], book, prices, level=level)
    assert dropped == ()
    assert over[0].notional_usd == cap
    assert over[0].clip_reason == f"clipped: max order {pct}%"


@pytest.mark.parametrize("level", LEVELS)
def test_position_cap_uses_market_value_and_clips_one_cent(level: str) -> None:
    limits = RISK_LIMITS[level]
    position_cap = Decimal("1000") * Decimal(int(limits["max_position_pct"])) / _HUNDRED
    existing = position_cap - Decimal("50")
    book = PortfolioSnapshot(
        Decimal("1000") - existing,
        (PositionSnapshot("AAPL", existing / Decimal("10"), Decimal("1")),),
    )
    prices = {"AAPL": Decimal("10")}
    assert _equity(book, prices) == Decimal("1000")

    at_limit, dropped = _apply([_proposal("AAPL", "buy", "5")], book, prices, level=level)
    assert dropped == ()
    assert at_limit[0].notional_usd == Decimal("50")
    assert at_limit[0].clip_reason is None

    over, dropped = _apply([_proposal("AAPL", "buy", "5.001")], book, prices, level=level)
    assert dropped == ()
    assert over[0].notional_usd == Decimal("50")
    assert over[0].clip_reason == f"clipped: max position {limits['max_position_pct']}%"

    full = PortfolioSnapshot(
        Decimal("1000") - position_cap,
        (PositionSnapshot("AAPL", position_cap / Decimal("10"), Decimal("1")),),
    )
    orders, dropped = _apply([_proposal("AAPL", "buy", "1")], full, prices, level=level)
    assert orders == ()
    assert dropped[0].reason == f"clipped: max position {limits['max_position_pct']}%"


@pytest.mark.parametrize("level", LEVELS)
def test_cash_buffer_allows_the_limit_and_clips_one_cent(level: str) -> None:
    buffer = Decimal("1000") * Decimal(int(RISK_LIMITS[level]["min_cash_buffer_pct"])) / _HUNDRED
    cash = buffer + Decimal("50")
    other = Decimal("1000") - cash
    book = PortfolioSnapshot(cash, (PositionSnapshot("MSFT", other / Decimal("10"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    assert _equity(book, prices) == Decimal("1000")

    at_limit, dropped = _apply([_proposal("AAPL", "buy", "5")], book, prices, level=level)
    assert dropped == ()
    assert at_limit[0].notional_usd == Decimal("50")
    assert at_limit[0].clip_reason is None
    assert cash - Decimal("50") == buffer

    over, dropped = _apply([_proposal("AAPL", "buy", "5.001")], book, prices, level=level)
    assert dropped == ()
    assert over[0].notional_usd == Decimal("50")
    assert over[0].clip_reason == "clipped: cash buffer"
    assert cash - Decimal("50") == buffer


def test_commission_is_reserved_for_every_buy() -> None:
    commission = Decimal("0.25")
    book = PortfolioSnapshot(Decimal("200"), (PositionSnapshot("MSFT", Decimal("80"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "NVDA": Decimal("10"), "MSFT": Decimal("10")}
    proposals = [_proposal("AAPL", "buy", "8", "0.9"), _proposal("NVDA", "buy", "5", "0.2")]
    orders, dropped = _apply(proposals, book, prices, settings=_settings(COMMISSION_USD=commission))
    assert dropped == ()
    assert [order.ticker for order in orders] == ["AAPL", "NVDA"]
    assert orders[0].notional_usd == Decimal("80")
    assert orders[0].clip_reason is None
    assert orders[1].notional_usd == Decimal("19.50")
    assert orders[1].clip_reason == "clipped: cash buffer"
    spent = sum((order.notional_usd or Decimal("0")) + commission for order in orders)
    assert Decimal("200") - spent == Decimal("100")


def test_a_buy_that_lands_exactly_on_cash_after_commission_is_kept() -> None:
    commission = Decimal("0.25")
    book = PortfolioSnapshot(Decimal("150"), (PositionSnapshot("MSFT", Decimal("85"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    settings = _settings(COMMISSION_USD=commission)
    exact, dropped = _apply([_proposal("AAPL", "buy", "4.975")], book, prices, settings=settings)
    assert dropped == ()
    assert exact[0].notional_usd == Decimal("49.75")
    assert exact[0].clip_reason is None
    assert Decimal("150") - (Decimal("49.75") + commission) == Decimal("100")

    over, dropped = _apply([_proposal("AAPL", "buy", "4.976")], book, prices, settings=settings)
    assert dropped == ()
    assert over[0].notional_usd == Decimal("49.75")
    assert over[0].clip_reason == "clipped: cash buffer"


def test_limited_cash_goes_to_the_higher_confidence_buy() -> None:
    book = PortfolioSnapshot(Decimal("200"), (PositionSnapshot("MSFT", Decimal("80"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "NVDA": Decimal("10"), "MSFT": Decimal("10")}
    listed_low_first = [_proposal("NVDA", "buy", "15", "0.1"), _proposal("AAPL", "buy", "15", "0.9")]
    orders, dropped = _apply(listed_low_first, book, prices)
    assert [order.ticker for order in orders] == ["AAPL"]
    assert orders[0].notional_usd == Decimal("100")
    assert orders[0].clip_reason == "clipped: cash buffer"
    assert dropped[0].ticker == "NVDA"
    assert dropped[0].reason == "clipped: cash buffer"


def test_equal_confidence_gives_the_only_cash_to_the_earlier_ticker() -> None:
    book = PortfolioSnapshot(Decimal("200"), (PositionSnapshot("NVDA", Decimal("80"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10"), "NVDA": Decimal("10")}
    listed_later_ticker_first = [
        _proposal("MSFT", "buy", "15", "0.5"),
        _proposal("AAPL", "buy", "15", "0.5"),
    ]
    orders, dropped = _apply(listed_later_ticker_first, book, prices)
    assert [order.ticker for order in orders] == ["AAPL"]
    assert dropped[0].ticker == "MSFT"


@pytest.mark.parametrize("level", LEVELS)
def test_position_count_allows_the_limit_and_a_sell_does_not_free_a_slot(level: str) -> None:
    limit = int(RISK_LIMITS[level]["max_positions"])
    held = NAMES[:limit]
    extra = NAMES[limit]
    another = NAMES[limit + 1]

    def book_of(count: int) -> tuple[PortfolioSnapshot, dict[str, Decimal]]:
        positions = tuple(PositionSnapshot(NAMES[i], Decimal("1"), Decimal("10")) for i in range(count))
        cash = Decimal("1000") - Decimal(count) * Decimal("10")
        prices = {NAMES[i]: Decimal("10") for i in range(count + 2)}
        return PortfolioSnapshot(cash, positions), prices

    under, prices = book_of(limit - 1)
    orders, dropped = _apply([_proposal(extra, "buy", "1")], under, prices, level=level)
    assert dropped == ()
    assert orders[0].ticker == extra

    full, prices = book_of(limit)
    orders, dropped = _apply([_proposal(extra, "buy", "1")], full, prices, level=level)
    assert orders == ()
    assert dropped[0].reason == "max positions"

    added, dropped = _apply([_proposal(held[0], "buy", "1")], full, prices, level=level)
    assert dropped == ()
    assert added[0].ticker == held[0]

    orders, dropped = _apply(
        [_proposal(held[0], "sell", "100"), _proposal(extra, "buy", "1")],
        full,
        prices,
        level=level,
    )
    assert [(order.ticker, order.side) for order in orders] == [(held[0], "sell")]
    assert orders[0].qty == Decimal("1")
    assert dropped[0].reason == "max positions"
    assert dropped[0].ticker == extra

    low_first = [_proposal(extra, "buy", "1", "0.1"), _proposal(another, "buy", "1", "0.9")]
    orders, dropped = _apply(low_first, under, prices, level=level)
    assert [order.ticker for order in orders] == [another]
    assert dropped[0].ticker == extra
    assert dropped[0].reason == "max positions"


@pytest.mark.parametrize("level", LEVELS)
def test_every_accepted_buy_counts_toward_the_daily_cap(level: str) -> None:
    limit = int(RISK_LIMITS[level]["max_new_buys_per_day"])
    book = PortfolioSnapshot(Decimal("980"), (PositionSnapshot("AAPL", Decimal("2"), Decimal("10")),))
    prices = {"AAPL": Decimal("10")}
    proposals = [
        _proposal("AAPL", "buy", "1", Decimal("0.90") - Decimal(index) / _HUNDRED)
        for index in range(limit + 1)
    ]
    orders, dropped = _apply(proposals, book, prices, level=level)
    assert len(orders) == limit
    assert [item.reason for item in dropped] == ["max buys per day"]

    one_left, dropped = _apply(proposals[:2], book, prices, level=level, buys_today=limit - 1)
    assert len(one_left) == 1
    assert dropped[0].reason == "max buys per day"

    none, dropped = _apply(proposals[:1], book, prices, level=level, buys_today=limit)
    assert none == ()
    assert dropped[0].reason == "max buys per day"


def test_the_daily_cap_and_the_position_cap_win_over_the_dollar_minimum() -> None:
    book = PortfolioSnapshot(Decimal("1000"))
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    orders, dropped = _apply([_proposal("AAPL", "buy", "0.1")], book, prices, buys_today=4)
    assert orders == ()
    assert dropped[0].reason == "max buys per day"

    held = tuple(PositionSnapshot(NAMES[i], Decimal("1"), Decimal("10")) for i in range(8))
    full = PortfolioSnapshot(Decimal("920"), held)
    full_prices = {NAMES[i]: Decimal("10") for i in range(9)}
    orders, dropped = _apply([_proposal(NAMES[8], "buy", "0.1")], full, full_prices, level="balanced")
    assert orders == ()
    assert dropped[0].reason == "max positions"


def test_five_dollar_minimum_comes_from_settings() -> None:
    book = PortfolioSnapshot(Decimal("1000"))
    prices = {"AAPL": Decimal("10")}
    kept, dropped = _apply([_proposal("AAPL", "buy", "0.5")], book, prices)
    assert dropped == ()
    assert kept[0].notional_usd == Decimal("5")

    orders, dropped = _apply([_proposal("AAPL", "buy", "0.499")], book, prices)
    assert orders == ()
    assert dropped[0].reason == "below $5 minimum"

    orders, dropped = _apply(
        [_proposal("AAPL", "buy", "1")],
        book,
        prices,
        settings=_settings(MIN_ORDER_USD=Decimal("20")),
    )
    assert orders == ()
    assert dropped[0].reason == "below $20 minimum"
    kept, dropped = _apply(
        [_proposal("AAPL", "buy", "2")],
        book,
        prices,
        settings=_settings(MIN_ORDER_USD=Decimal("20")),
    )
    assert dropped == ()
    assert kept[0].notional_usd == Decimal("20")


def test_a_clip_down_to_five_dollars_is_kept_and_one_cent_under_is_named() -> None:
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    exact_book = PortfolioSnapshot(Decimal("105"), (PositionSnapshot("MSFT", Decimal("89.5"), Decimal("10")),))
    kept, dropped = _apply([_proposal("AAPL", "buy", "5")], exact_book, prices)
    assert dropped == ()
    assert kept[0].notional_usd == Decimal("5")
    assert kept[0].clip_reason == "clipped: cash buffer"

    under_book = PortfolioSnapshot(
        Decimal("104.99"),
        (PositionSnapshot("MSFT", Decimal("89.501"), Decimal("10")),),
    )
    orders, dropped = _apply([_proposal("AAPL", "buy", "5")], under_book, prices)
    assert orders == ()
    assert dropped[0].reason == "clipped: cash buffer"

    small = PortfolioSnapshot(Decimal("40"))
    orders, dropped = _apply([_proposal("AAPL", "buy", "10")], small, prices, level="conservative")
    assert orders == ()
    assert dropped[0].reason == "below $5 minimum"
    orders, dropped = _apply([_proposal("AAPL", "buy", "10.025")], small, prices, level="conservative")
    assert orders == ()
    assert dropped[0].reason == "clipped: max order 10%"


def test_sell_proceeds_are_not_cash_and_a_losing_position_is_not_auto_sold() -> None:
    book = PortfolioSnapshot(Decimal("0"), (PositionSnapshot("AAPL", Decimal("100"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    orders, dropped = _apply(
        [_proposal("AAPL", "sell", "100", "0.2"), _proposal("MSFT", "buy", "5", "0.9")],
        book,
        prices,
    )
    assert [(order.ticker, order.side, order.qty) for order in orders] == [("AAPL", "sell", Decimal("100"))]
    assert dropped[0].reason == "clipped: cash buffer"

    quiet, dropped = _apply([], book, {"AAPL": Decimal("1")})
    assert quiet == ()
    assert dropped == ()


def test_a_full_sell_of_eight_names_does_not_make_room_for_a_new_buy() -> None:
    held = NAMES[:8]
    newbie = NAMES[8]
    book = PortfolioSnapshot(
        Decimal("920"),
        tuple(PositionSnapshot(name, Decimal("1"), Decimal("10")) for name in held),
    )
    prices = {name: Decimal("10") for name in (*held, newbie)}
    orders, dropped = _apply(
        [_proposal(held[0], "sell", "100"), _proposal(newbie, "buy", "5", "0.9")],
        book,
        prices,
        level="balanced",
    )
    assert [(order.ticker, order.side) for order in orders] == [(held[0], "sell")]
    assert dropped[0].ticker == newbie
    assert dropped[0].reason == "max positions"


def test_buy_and_sell_of_the_same_ticker_keeps_the_sell() -> None:
    book = PortfolioSnapshot(Decimal("500"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("20")),))
    prices = {"AAPL": Decimal("20")}
    orders, dropped = _apply(
        [_proposal("AAPL", "buy", "10", "0.99"), _proposal("AAPL", "sell", "50", "0.1")],
        book,
        prices,
    )
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].qty == Decimal("5")
    assert dropped[0].action == "buy"
    assert dropped[0].reason == "conflicting proposals"

    orders, dropped = _apply(
        [_proposal("AAPL", "buy", "10"), _proposal("AAPL", "sell", "50")],
        book,
        prices,
        exclusions=Exclusions(tickers=frozenset({"AAPL"})),
    )
    assert orders[0].side == "sell"
    assert dropped[0].reason == "excluded"


def test_later_buys_are_clipped_against_buys_already_accepted() -> None:
    book = PortfolioSnapshot(Decimal("1000"))
    prices = {"AAPL": Decimal("10")}
    proposals = [
        _proposal("AAPL", "buy", "15", "0.9"),
        _proposal("AAPL", "buy", "15", "0.5"),
        _proposal("AAPL", "buy", "15", "0.1"),
    ]
    orders, dropped = _apply(proposals, book, prices, level="balanced")
    assert [order.notional_usd for order in orders] == [Decimal("150"), Decimal("100")]
    assert orders[0].clip_reason is None
    assert orders[1].clip_reason == "clipped: max position 25%"
    assert dropped[0].reason == "clipped: max position 25%"
    assert sum(order.notional_usd or Decimal("0") for order in orders) == Decimal("250")


def test_sells_of_one_ticker_never_exceed_the_shares_held() -> None:
    book = PortfolioSnapshot(Decimal("100"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("20")),))
    prices = {"AAPL": Decimal("20")}
    orders, dropped = _apply(
        [_proposal("AAPL", "sell", "60"), _proposal("AAPL", "sell", "60"), _proposal("AAPL", "sell", "10")],
        book,
        prices,
    )
    assert [order.qty for order in orders] == [Decimal("6"), Decimal("4")]
    assert orders[0].clip_reason is None
    assert orders[1].clip_reason == "clipped: exceeds held"
    assert sum(order.qty or Decimal("0") for order in orders) == Decimal("10")
    assert dropped[0].reason == "exceeds held"


def test_sell_percent_floors_to_six_places_and_a_clipped_remainder_names_the_clip() -> None:
    dusty = PortfolioSnapshot(Decimal("100"), (PositionSnapshot("AAPL", Decimal("1.0000009"), Decimal("100")),))
    orders, dropped = _apply([_proposal("AAPL", "sell", "100")], dusty, {"AAPL": Decimal("100")})
    assert dropped == ()
    assert orders[0].qty == Decimal("1.000000")
    assert orders[0].qty is not None
    assert orders[0].qty <= Decimal("1.0000009")

    half, dropped = _apply([_proposal("AAPL", "sell", "50")], dusty, {"AAPL": Decimal("100")})
    assert dropped == ()
    assert half[0].qty == Decimal("0.500000")

    book = PortfolioSnapshot(Decimal("100"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("10")),))
    orders, dropped = _apply(
        [_proposal("AAPL", "sell", "99.9"), _proposal("AAPL", "sell", "50")],
        book,
        {"AAPL": Decimal("10")},
    )
    assert orders[0].qty == Decimal("9.990000")
    assert dropped[0].reason == "clipped: exceeds held"

    dust = PortfolioSnapshot(Decimal("100"), (PositionSnapshot("AAPL", Decimal("0.0000004"), Decimal("10")),))
    orders, dropped = _apply([_proposal("AAPL", "sell", "100")], dust, {"AAPL": Decimal("100000")})
    assert orders == ()
    assert dropped[0].reason == "size rounds to zero"


def test_gates_run_in_order_and_sells_of_held_names_still_exit() -> None:
    book = PortfolioSnapshot(
        Decimal("600"),
        (
            PositionSnapshot("XOM", Decimal("10"), Decimal("20")),
            PositionSnapshot("GONE", Decimal("2"), Decimal("20")),
        ),
    )
    prices = {"XOM": Decimal("20"), "AAPL": Decimal("10"), "ZZZZ": Decimal("10"), "GONE": Decimal("20"), "MSFT": Decimal("10")}
    orders, dropped = _apply(
        [
            _proposal("AAPL", "buy", "5"),
            _proposal("XOM", "buy", "5"),
            _proposal("XOM", "sell", "50"),
            _proposal("GONE", "sell", "50"),
            _proposal("ZZZZ", "buy", "5"),
            _proposal("ZZZZ", "sell", "50"),
            _proposal("MSFT", "buy", "5"),
        ],
        book,
        prices,
        interest=("XOM", "GONE"),
        exclusions=Exclusions(tickers=frozenset({"AAPL"}), sectors=frozenset({"Energy"})),
    )
    assert [(order.ticker, order.side) for order in orders] == [("XOM", "sell"), ("GONE", "sell")]
    assert orders[0].qty == Decimal("5")
    assert {(item.ticker, item.action): item.reason for item in dropped} == {
        ("AAPL", "buy"): "excluded",
        ("XOM", "buy"): "excluded",
        ("ZZZZ", "buy"): "not in universe",
        ("ZZZZ", "sell"): "not held",
        ("MSFT", "buy"): "outside interests",
    }

    orders, dropped = _apply(
        [_proposal("ZZZZ", "buy", "5"), _proposal("AAPL", "buy", "5")],
        PortfolioSnapshot(Decimal("1000")),
        {"ZZZZ": Decimal("10"), "AAPL": Decimal("10")},
        interest=(),
    )
    assert {item.ticker: item.reason for item in dropped} == {
        "ZZZZ": "not in universe",
        "AAPL": "outside interests",
    }
    assert orders == ()

    orders, dropped = _apply(
        [_proposal("ZZZZ", "buy", "5")],
        PortfolioSnapshot(Decimal("1000")),
        {"ZZZZ": Decimal("10")},
        exclusions=Exclusions(tickers=frozenset({"ZZZZ"})),
    )
    assert dropped[0].reason == "excluded"

    orders, dropped = _apply(
        [_proposal("AAPL", "buy", "5")],
        PortfolioSnapshot(Decimal("1000")),
        {"AAPL": Decimal("10")},
        interest=(),
        exclusions=Exclusions(tickers=frozenset({"AAPL"})),
    )
    assert dropped[0].reason == "excluded"


@pytest.mark.parametrize("level", LEVELS)
def test_etf_buy_is_allowed_at_every_shipped_level(level: str) -> None:
    assert RISK_LIMITS[level]["etfs_allowed"] is True
    orders, dropped = _apply(
        [_proposal("SPY", "buy", "1")],
        PortfolioSnapshot(Decimal("1000")),
        {"SPY": Decimal("10")},
        level=level,
    )
    assert dropped == ()
    assert orders[0].ticker == "SPY"
    assert orders[0].system is False


@pytest.mark.parametrize(
    ("action", "size"),
    [("buy", "0"), ("buy", "-1"), ("sell", "0"), ("sell", "-1"), ("sell", "100.01"), ("hold", "10")],
)
def test_invalid_size_is_dropped(action: str, size: str) -> None:
    book = PortfolioSnapshot(Decimal("800"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("20")),))
    orders, dropped = _apply([_proposal("AAPL", action, size)], book, {"AAPL": Decimal("20")})
    assert orders == ()
    assert dropped[0].reason == "invalid size"


def test_a_full_sell_is_allowed_and_a_buy_over_one_hundred_percent_is_clipped() -> None:
    book = PortfolioSnapshot(Decimal("800"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("20")),))
    sold, dropped = _apply([_proposal("AAPL", "sell", "100")], book, {"AAPL": Decimal("20")})
    assert dropped == ()
    assert sold[0].qty == Decimal("10")

    bought, dropped = _apply(
        [_proposal("AAPL", "buy", "500")],
        PortfolioSnapshot(Decimal("1000")),
        {"AAPL": Decimal("10")},
        level="balanced",
    )
    assert dropped == ()
    assert bought[0].notional_usd == Decimal("150")
    assert bought[0].clip_reason == "clipped: max order 15%"


def test_a_sell_under_five_dollars_is_dropped_and_sells_keep_input_order() -> None:
    book = PortfolioSnapshot(
        Decimal("600"),
        (
            PositionSnapshot("MSFT", Decimal("5"), Decimal("20")),
            PositionSnapshot("AAPL", Decimal("1"), Decimal("4")),
        ),
    )
    prices = {"AAPL": Decimal("4"), "MSFT": Decimal("20"), "NVDA": Decimal("10")}
    orders, dropped = _apply(
        [
            _proposal("NVDA", "buy", "1", "0.99"),
            _proposal("MSFT", "sell", "40", "0.1"),
            _proposal("AAPL", "sell", "100", "0.8"),
        ],
        book,
        prices,
    )
    assert [(order.ticker, order.side) for order in orders] == [("MSFT", "sell"), ("NVDA", "buy")]
    assert orders[0].qty == Decimal("2")
    assert dropped[0].ticker == "AAPL"
    assert dropped[0].reason == "below $5 minimum"


def test_missing_or_non_positive_price_raises_and_a_new_buy_can_omit_its_price() -> None:
    held = PortfolioSnapshot(Decimal("800"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("20")),))
    with pytest.raises(MissingPrice) as missing:
        _apply([_proposal("MSFT", "buy", "5")], held, {})
    assert missing.value.ticker == "AAPL"
    with pytest.raises(MissingPrice):
        _apply([_proposal("MSFT", "buy", "5")], held, {"AAPL": Decimal("0")})
    with pytest.raises(MissingPrice):
        _apply([_proposal("MSFT", "buy", "5")], held, {"AAPL": Decimal("-1")})
    with pytest.raises(MissingPrice):
        _apply([_proposal("MSFT", "buy", "5")], PortfolioSnapshot(Decimal("1000")), {"MSFT": Decimal("0")})

    orders, dropped = _apply([_proposal("MSFT", "buy", "1")], PortfolioSnapshot(Decimal("1000")), {})
    assert dropped == ()
    assert orders[0].notional_usd == Decimal("10")


def test_bad_inputs_are_refused() -> None:
    book = PortfolioSnapshot(Decimal("1000"))
    prices = {"AAPL": Decimal("10")}
    proposal = [_proposal("AAPL", "buy", "1")]
    with pytest.raises(ValueError, match="unknown risk level"):
        _apply(proposal, book, prices, level="nope")
    with pytest.raises(ValueError, match="buys_today"):
        _apply(proposal, book, prices, buys_today=-1)
    with pytest.raises(ValueError, match="buys_today"):
        _apply(proposal, book, prices, buys_today=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="COMMISSION_USD"):
        _apply(proposal, book, prices, settings=_settings(COMMISSION_USD=Decimal("-0.01")))
    with pytest.raises(ValueError, match="duplicate"):
        _apply(
            proposal,
            PortfolioSnapshot(
                Decimal("1000"),
                (
                    PositionSnapshot("AAPL", Decimal("1"), Decimal("1")),
                    PositionSnapshot("AAPL", Decimal("1"), Decimal("1")),
                ),
            ),
            prices,
        )
    with pytest.raises(ValueError, match="quantity"):
        _apply(
            proposal,
            PortfolioSnapshot(Decimal("1000"), (PositionSnapshot("AAPL", Decimal("0"), Decimal("1")),)),
            prices,
        )
    with pytest.raises(ValueError, match="unknown risk level"):
        check_stop_losses([], {}, "nope")


def test_clip_reason_names_the_limit_that_set_the_size() -> None:
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    position_binds = PortfolioSnapshot(
        Decimal("180"),
        (
            PositionSnapshot("AAPL", Decimal("20"), Decimal("1")),
            PositionSnapshot("MSFT", Decimal("62"), Decimal("10")),
        ),
    )
    orders, dropped = _apply([_proposal("AAPL", "buy", "10")], position_binds, prices)
    assert dropped == ()
    assert orders[0].notional_usd == Decimal("50")
    assert orders[0].clip_reason == "clipped: max position 25%"

    cash_binds = PortfolioSnapshot(
        Decimal("140"),
        (
            PositionSnapshot("AAPL", Decimal("20"), Decimal("1")),
            PositionSnapshot("MSFT", Decimal("66"), Decimal("10")),
        ),
    )
    orders, dropped = _apply([_proposal("AAPL", "buy", "10")], cash_binds, prices)
    assert dropped == ()
    assert orders[0].notional_usd == Decimal("40")
    assert orders[0].clip_reason == "clipped: cash buffer"


def test_negative_cash_does_not_fund_a_buy() -> None:
    book = PortfolioSnapshot(Decimal("-1"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("10")),))
    orders, dropped = _apply(
        [_proposal("AAPL", "sell", "100"), _proposal("MSFT", "buy", "5")],
        book,
        {"AAPL": Decimal("10"), "MSFT": Decimal("10")},
    )
    assert [(order.ticker, order.side) for order in orders] == [("AAPL", "sell")]
    assert dropped[0].reason == "clipped: cash buffer"


@pytest.mark.parametrize(
    ("level", "threshold"),
    [("conservative", Decimal("92")), ("balanced", Decimal("88")), ("aggressive", Decimal("80"))],
)
def test_stop_loss_sells_at_the_threshold_and_not_one_cent_above(level: str, threshold: Decimal) -> None:
    position = PositionSnapshot("AAPL", Decimal("3"), Decimal("100"))
    at_stop = check_stop_losses([position], {"AAPL": threshold}, level)
    assert len(at_stop) == 1
    assert at_stop[0].side == "sell"
    assert at_stop[0].qty == Decimal("3")
    assert at_stop[0].system is True
    assert at_stop[0].validated is True
    assert at_stop[0].summary == f"Stop-loss at {RISK_LIMITS[level]['stop_loss_pct']}%"
    assert "−" not in (at_stop[0].summary or "")
    assert at_stop[0].graph_path == ()
    assert at_stop[0].news_ids == ()

    assert check_stop_losses([position], {"AAPL": threshold + Decimal("0.01")}, level) == ()
    below = check_stop_losses([position], {"AAPL": threshold - Decimal("0.01")}, level)
    assert len(below) == 1
    assert below[0].qty == Decimal("3")


def test_stop_loss_sells_a_tiny_position_and_keeps_the_full_quantity() -> None:
    tiny = check_stop_losses(
        [PositionSnapshot("AAPL", Decimal("0.01"), Decimal("100"))],
        {"AAPL": Decimal("88")},
        "balanced",
    )
    assert tiny[0].qty == Decimal("0.01")
    assert tiny[0].notional_usd == Decimal("0.88")
    assert tiny[0].notional_usd is not None
    assert tiny[0].notional_usd < Decimal("5")

    odd = Decimal("1.1234567")
    sold = check_stop_losses([PositionSnapshot("AAPL", odd, Decimal("10"))], {"AAPL": Decimal("8")}, "aggressive")
    assert sold[0].qty == odd

    mixed = check_stop_losses(
        [
            PositionSnapshot("AAPL", Decimal("2"), Decimal("100")),
            PositionSnapshot("MSFT", Decimal("4"), Decimal("50")),
        ],
        {"AAPL": Decimal("92"), "MSFT": Decimal("47")},
        "conservative",
    )
    assert [order.ticker for order in mixed] == ["AAPL"]

    with pytest.raises(MissingPrice):
        check_stop_losses([PositionSnapshot("AAPL", Decimal("1"), Decimal("10"))], {}, "balanced")
    with pytest.raises(MissingPrice):
        check_stop_losses(
            [PositionSnapshot("AAPL", Decimal("1"), Decimal("10"))],
            {"AAPL": Decimal("0")},
            "balanced",
        )
    with pytest.raises(ValueError, match="average cost"):
        check_stop_losses(
            [PositionSnapshot("AAPL", Decimal("1"), Decimal("0"))],
            {"AAPL": Decimal("1")},
            "balanced",
        )
    with pytest.raises(ValueError, match="duplicate"):
        check_stop_losses(
            [
                PositionSnapshot("AAPL", Decimal("1"), Decimal("10")),
                PositionSnapshot("AAPL", Decimal("1"), Decimal("10")),
            ],
            {"AAPL": Decimal("8")},
            "aggressive",
        )


def test_player_buy_must_be_in_the_universe_and_a_departed_sell_is_allowed() -> None:
    cash = PortfolioSnapshot(Decimal("100"))
    prices = {"ZZZZ": Decimal("10"), "AAPL": Decimal("10"), "SPY": Decimal("10")}
    settings = _settings()
    unknown = validate_player_order(
        PlayerOrder("ZZZZ", "buy", notional_usd=Decimal("10")),
        cash,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(unknown, Dropped)
    assert unknown.reason == "not in universe"

    allowed = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("10")),
        cash,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(allowed, RiskOrder)
    assert allowed.notional_usd == Decimal("10")
    assert allowed.system is False
    assert allowed.clip_reason is None

    etf = validate_player_order(
        PlayerOrder("SPY", "buy", notional_usd=Decimal("10")),
        cash,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(etf, RiskOrder)

    held = PortfolioSnapshot(Decimal("100"), (PositionSnapshot("GONE", Decimal("2"), Decimal("20")),))
    gone_prices = {"GONE": Decimal("20"), "AAPL": Decimal("10")}
    exit_order = validate_player_order(
        PlayerOrder("GONE", "sell", qty=Decimal("2")),
        held,
        gone_prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(exit_order, RiskOrder)
    assert exit_order.qty == Decimal("2")
    too_many = validate_player_order(
        PlayerOrder("GONE", "sell", qty=Decimal("2.000001")),
        held,
        gone_prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(too_many, Dropped)
    assert too_many.reason == "exceeds held"
    not_held = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=Decimal("1")),
        held,
        gone_prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(not_held, Dropped)
    assert not_held.reason == "not held"


def test_player_prices_must_exist_and_be_positive() -> None:
    book = PortfolioSnapshot(Decimal("100"))
    order = PlayerOrder("AAPL", "buy", notional_usd=Decimal("10"))
    for prices in ({}, {"AAPL": Decimal("0")}, {"AAPL": Decimal("-1")}):
        with pytest.raises(MissingPrice):
            validate_player_order(order, book, prices, UNIVERSE, _settings())


def test_player_cash_includes_commission_and_the_five_dollar_minimum() -> None:
    book = PortfolioSnapshot(Decimal("10"))
    prices = {"AAPL": Decimal("10")}
    settings = _settings(COMMISSION_USD=Decimal("0.25"))
    exact = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("9.75")),
        book,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(exact, RiskOrder)
    over = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("9.76")),
        book,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(over, Dropped)
    assert over.reason == "insufficient cash"

    small = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("4.99")),
        PortfolioSnapshot(Decimal("100")),
        prices,
        UNIVERSE,
        _settings(),
    )
    assert isinstance(small, Dropped)
    assert small.reason == "below $5 minimum"
    boundary = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("5")),
        PortfolioSnapshot(Decimal("100")),
        prices,
        UNIVERSE,
        _settings(),
    )
    assert isinstance(boundary, RiskOrder)

    priced = {"AAPL": Decimal("5")}
    held = PortfolioSnapshot(Decimal("0"), (PositionSnapshot("AAPL", Decimal("1"), Decimal("10")),))
    sold = validate_player_order(PlayerOrder("AAPL", "sell", qty=Decimal("1")), held, priced, UNIVERSE, _settings())
    assert isinstance(sold, RiskOrder)
    cheap = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=Decimal("1")),
        held,
        {"AAPL": Decimal("4.99")},
        UNIVERSE,
        _settings(),
    )
    assert isinstance(cheap, Dropped)
    assert cheap.reason == "below $5 minimum"
    invalid = validate_player_order(PlayerOrder("AAPL", "buy"), PortfolioSnapshot(Decimal("100")), prices, UNIVERSE, _settings())
    assert isinstance(invalid, Dropped)
    assert invalid.reason == "invalid size"


def test_player_order_ignores_position_limits() -> None:
    book = PortfolioSnapshot(Decimal("500"), (PositionSnapshot("MSFT", Decimal("50"), Decimal("10")),))
    prices = {"AAPL": Decimal("10"), "MSFT": Decimal("10")}
    order = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("400")),
        book,
        prices,
        UNIVERSE,
        _settings(),
    )
    assert isinstance(order, RiskOrder)
    assert order.notional_usd == Decimal("400")


def test_agent_orders_are_not_marked_system() -> None:
    orders, dropped = _apply([_proposal("AAPL", "buy", "1")], PortfolioSnapshot(Decimal("1000")), {"AAPL": Decimal("10")})
    assert dropped == ()
    assert orders[0].system is False
    assert orders[0].validated is False
    assert orders[0].summary is None


def test_risk_engine_does_not_touch_the_database_or_the_clock() -> None:
    from pathlib import Path

    import app.league.sim.risk as risk

    source = Path(risk.__file__).read_text(encoding="utf-8")
    for token in ("sqlalchemy", "datetime", "time.time", "Session", "connect("):
        assert token not in source


_KNOWN_REASONS = {
    "invalid size",
    "excluded",
    "not in universe",
    "outside interests",
    "etf not allowed",
    "conflicting proposals",
    "not held",
    "size rounds to zero",
    "exceeds held",
    "clipped: exceeds held",
    "max positions",
    "max buys per day",
    "below $5 minimum",
    "clipped: cash buffer",
}


@st.composite
def _books(draw: st.DrawFn) -> tuple:
    level = draw(st.sampled_from(LEVELS))
    names = draw(st.lists(st.sampled_from(INTEREST), min_size=0, max_size=6, unique=True))
    positions = []
    prices: dict[str, Decimal] = {}
    for name in names:
        qty = draw(st.decimals(min_value=Decimal("0.000001"), max_value=Decimal("30"), places=6))
        price = draw(st.decimals(min_value=Decimal("1"), max_value=Decimal("200"), places=2))
        average = draw(st.decimals(min_value=Decimal("1"), max_value=Decimal("200"), places=2))
        positions.append(PositionSnapshot(name, qty, average))
        prices[name] = price
    cash = draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("2000"), places=2))
    count = draw(st.integers(min_value=0, max_value=5))
    proposals = []
    pool = list(INTEREST) + ["ZZZZ", "NOPE"]
    for _ in range(count):
        ticker = draw(st.sampled_from(pool))
        action = draw(st.sampled_from(["buy", "sell"]))
        size = draw(st.decimals(min_value=Decimal("-1"), max_value=Decimal("120"), places=2))
        confidence = draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2))
        proposals.append(Proposal(ticker, action, size, confidence))
        if ticker not in prices:
            prices[ticker] = draw(st.decimals(min_value=Decimal("1"), max_value=Decimal("200"), places=2))
    excluded = frozenset(draw(st.lists(st.sampled_from(INTEREST), max_size=3, unique=True)))
    sectors = frozenset(
        draw(st.lists(st.sampled_from(["ETF", "Energy", "Financials", "Information Technology"]), max_size=2, unique=True))
    )
    interest = draw(st.lists(st.sampled_from(INTEREST), max_size=len(INTEREST), unique=True))
    buys_today = draw(st.integers(min_value=0, max_value=8))
    commission = draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2))
    portfolio = PortfolioSnapshot(cash, tuple(positions))
    return level, portfolio, prices, proposals, Exclusions(excluded, sectors), interest, buys_today, commission


@settings(deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(case=_books())
def test_risk_properties_hold_for_random_books(case: tuple) -> None:
    level, portfolio, prices, proposals, exclusions, interest, buys_today, commission = case
    settings_ = _settings(COMMISSION_USD=commission, MIN_ORDER_USD=Decimal("5"))
    orders, dropped = apply_risk(
        proposals,
        portfolio,
        prices,
        level,
        UNIVERSE,
        interest,
        exclusions,
        buys_today,
        settings_,
    )
    assert Counter(proposal.action for proposal in proposals) == Counter(
        [*(order.side for order in orders), *(item.action for item in dropped)]
    ), (orders, dropped)
    for item in dropped:
        assert (
            item.reason in _KNOWN_REASONS
            or item.reason.startswith("clipped: max order ")
            or item.reason.startswith("clipped: max position ")
        )

    limits = RISK_LIMITS[level]
    equity = _equity(portfolio, prices)
    buffer = equity * Decimal(int(limits["min_cash_buffer_pct"])) / _HUNDRED
    order_cap = equity * Decimal(int(limits["max_single_order_pct"])) / _HUNDRED
    position_cap = equity * Decimal(int(limits["max_position_pct"])) / _HUNDRED
    interest_set = set(interest)
    sell_names = {proposal.ticker for proposal in proposals if proposal.action == "sell"}
    starting = {position.ticker: position.qty for position in portfolio.positions}
    buy_orders = [order for order in orders if order.side == "buy"]
    sell_orders = [order for order in orders if order.side == "sell"]

    spent = Decimal("0")
    for order in buy_orders:
        assert order.notional_usd is not None
        assert order.notional_usd >= Decimal("5")
        assert order.notional_usd <= order_cap
        assert order.system is False
        assert order.ticker in interest_set
        assert order.ticker not in exclusions.tickers
        assert order.ticker not in sell_names
        listing = UNIVERSE.get(order.ticker)
        assert listing.sector not in exclusions.sectors
        spent += order.notional_usd + commission
    if portfolio.cash >= buffer:
        assert portfolio.cash - spent >= buffer
    else:
        assert spent == 0

    added: dict[str, Decimal] = {}
    for order in buy_orders:
        assert order.notional_usd is not None
        added[order.ticker] = added.get(order.ticker, Decimal("0")) + order.notional_usd
    for ticker, total in added.items():
        existing = starting.get(ticker, Decimal("0")) * prices[ticker]
        assert existing + total <= max(existing, position_cap)

    opened = {order.ticker for order in buy_orders if order.ticker not in starting}
    assert len(set(starting) | opened) <= max(len(starting), int(limits["max_positions"]))
    assert len(buy_orders) <= max(0, int(limits["max_new_buys_per_day"]) - buys_today)

    sold: dict[str, Decimal] = {}
    for order in sell_orders:
        assert order.qty is not None and order.notional_usd is not None
        assert order.ticker in starting
        assert order.notional_usd >= Decimal("5")
        assert order.system is False
        sold[order.ticker] = sold.get(order.ticker, Decimal("0")) + order.qty
    for ticker, qty in sold.items():
        assert qty <= starting[ticker]


@settings(deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(
    level=st.sampled_from(LEVELS),
    rows=st.lists(
        st.tuples(
            st.sampled_from(INTEREST),
            st.decimals(min_value=Decimal("0.000001"), max_value=Decimal("20"), places=6),
            st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2),
            st.decimals(min_value=Decimal("-2"), max_value=Decimal("2"), places=2),
        ),
        min_size=0,
        max_size=4,
        unique_by=lambda row: row[0],
    ),
)
def test_stop_loss_properties(level: str, rows: list[tuple[str, Decimal, Decimal, Decimal]]) -> None:
    stop = int(RISK_LIMITS[level]["stop_loss_pct"])
    positions = []
    prices: dict[str, Decimal] = {}
    expected: set[str] = set()
    for ticker, qty, average, bump in rows:
        threshold = average * Decimal(100 + stop) / _HUNDRED
        price = threshold + bump
        if price <= 0:
            price = threshold
        positions.append(PositionSnapshot(ticker, qty, average))
        prices[ticker] = price
        if price <= threshold:
            expected.add(ticker)
    orders = check_stop_losses(positions, prices, level)
    assert {order.ticker for order in orders} == expected
    by_ticker = {position.ticker: position for position in positions}
    for order in orders:
        assert order.qty == by_ticker[order.ticker].qty
        assert order.system is True
        assert order.validated is True
        assert order.summary == f"Stop-loss at {stop}%"
        assert order.graph_path == ()
        assert order.news_ids == ()


@settings(deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(
    side=st.sampled_from(["buy", "sell"]),
    ticker=st.sampled_from(list(INTEREST) + ["ZZZZ"]),
    notional=st.decimals(min_value=Decimal("0"), max_value=Decimal("500"), places=2),
    qty=st.decimals(min_value=Decimal("0"), max_value=Decimal("20"), places=6),
    cash=st.decimals(min_value=Decimal("0"), max_value=Decimal("500"), places=2),
    held_qty=st.decimals(min_value=Decimal("0.000001"), max_value=Decimal("20"), places=6),
    price=st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2),
    commission=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2),
    held_here=st.booleans(),
)
def test_player_order_properties(
    side: str,
    ticker: str,
    notional: Decimal,
    qty: Decimal,
    cash: Decimal,
    held_qty: Decimal,
    price: Decimal,
    commission: Decimal,
    held_here: bool,
) -> None:
    positions = (PositionSnapshot(ticker, held_qty, price),) if held_here else ()
    book = PortfolioSnapshot(cash, positions)
    order = PlayerOrder(
        ticker,
        side,
        notional_usd=notional if side == "buy" else None,
        qty=qty if side == "sell" else None,
    )
    result = validate_player_order(order, book, {ticker: price}, UNIVERSE, _settings(COMMISSION_USD=commission))
    if isinstance(result, Dropped):
        assert result.reason in {"invalid size", "not in universe", "not held", "exceeds held", "below $5 minimum", "insufficient cash"}
        return
    assert result.system is False
    if side == "buy":
        assert result.notional_usd == notional
        assert notional >= Decimal("5")
        assert notional + commission <= cash
        UNIVERSE.get(ticker)
    else:
        assert held_here
        assert result.qty == qty
        assert qty <= held_qty
        assert qty * price >= Decimal("5")
