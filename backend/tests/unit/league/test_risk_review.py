"""Review probes for the step 7 risk engine. These tests do not change it."""

from __future__ import annotations

from collections import Counter
from decimal import ROUND_DOWN, Decimal

import pytest

from app.league.config import RISK_LIMITS, LeagueSettings
from app.league.sim import risk as risk_mod
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

UNIVERSE = load_universe("backend/tests/fixtures/universe_small.csv")
INTEREST = tuple(listing.ticker for listing in UNIVERSE.listings)
_CASH = Decimal("0.0001")
_HUNDRED = Decimal("100")


def _settings(**overrides: Decimal) -> LeagueSettings:
    values: dict[str, Decimal] = {
        "COMMISSION_USD": Decimal("0"),
        "MIN_ORDER_USD": Decimal("5"),
    }
    values.update(overrides)
    return LeagueSettings(**values)


def _floor(value: Decimal) -> Decimal:
    return value.quantize(_CASH, rounding=ROUND_DOWN)


def _proposal(ticker: str, action: str, size: str, confidence: str = "0.5") -> Proposal:
    return Proposal(ticker, action, Decimal(size), Decimal(confidence))


def _run(
    proposals: list[Proposal],
    portfolio: PortfolioSnapshot,
    prices: dict[str, Decimal],
    level: str = "balanced",
    interest: tuple[str, ...] = INTEREST,
    exclusions: Exclusions | None = None,
    settings: LeagueSettings | None = None,
    buys_today: int = 0,
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


def test_etf_buy_is_dropped_when_etfs_are_not_allowed_and_the_sell_still_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert all(row["etfs_allowed"] is True for row in RISK_LIMITS.values())
    for row in RISK_LIMITS.values():
        monkeypatch.setitem(row, "etfs_allowed", False)
    assert risk_mod.RISK_LIMITS is RISK_LIMITS
    assert RISK_LIMITS["balanced"]["etfs_allowed"] is False

    book = PortfolioSnapshot(Decimal("1000"), (PositionSnapshot("SPY", Decimal("2"), Decimal("100")),))
    prices = {"SPY": Decimal("100"), "AAPL": Decimal("10")}
    orders, dropped = _run(
        [
            _proposal("SPY", "sell", "50", "0.2"),
            _proposal("SPY", "buy", "5", "0.9"),
            _proposal("AAPL", "buy", "5", "0.8"),
        ],
        book,
        prices,
        interest=("SPY", "AAPL"),
    )
    assert [(order.ticker, order.side, order.qty) for order in orders if order.side == "sell"] == [
        ("SPY", "sell", Decimal("1"))
    ]
    buys = [order for order in orders if order.side == "buy"]
    assert [order.ticker for order in buys] == ["AAPL"]
    assert {(item.ticker, item.action): item.reason for item in dropped} == {
        ("SPY", "buy"): "etf not allowed"
    }


def test_shipped_etf_flag_is_restored_after_the_patch() -> None:
    assert all(row["etfs_allowed"] is True for row in RISK_LIMITS.values())
    orders, dropped = _run(
        [_proposal("SPY", "buy", "1")],
        PortfolioSnapshot(Decimal("1000")),
        {"SPY": Decimal("10")},
    )
    assert dropped == ()
    assert orders[0].ticker == "SPY"


def test_single_order_clip_floors_below_an_off_grid_cap() -> None:
    cash = Decimal("1000.0015")
    book = PortfolioSnapshot(cash)
    orders, dropped = _run(
        [_proposal("AAPL", "buy", "50")],
        book,
        {"AAPL": Decimal("9999999999.9999")},
        level="conservative",
    )
    cap = cash * Decimal(int(RISK_LIMITS["conservative"]["max_single_order_pct"])) / _HUNDRED
    assert dropped == ()
    assert orders[0].notional_usd == _floor(cap)
    assert orders[0].notional_usd is not None
    assert orders[0].notional_usd <= cap
    assert orders[0].clip_reason == "clipped: max order 10%"


def test_position_clip_floors_below_an_off_grid_room() -> None:
    price = Decimal("0.0001")
    qty = Decimal("100000.300000")
    existing = qty * price
    cash = Decimal("70.00025")
    book = PortfolioSnapshot(cash, (PositionSnapshot("AAPL", qty, Decimal("0.0001")),))
    orders, dropped = _run(
        [_proposal("AAPL", "buy", "50")],
        book,
        {"AAPL": price},
        level="balanced",
    )
    equity = cash + existing
    position_cap = equity * Decimal(int(RISK_LIMITS["balanced"]["max_position_pct"])) / _HUNDRED
    order_cap = equity * Decimal(int(RISK_LIMITS["balanced"]["max_single_order_pct"])) / _HUNDRED
    room = position_cap - existing
    assert room < order_cap
    assert dropped == ()
    assert orders[0].notional_usd == _floor(room)
    assert orders[0].notional_usd is not None
    assert existing + orders[0].notional_usd <= position_cap
    assert orders[0].notional_usd <= order_cap
    assert orders[0].clip_reason == "clipped: max position 25%"


def test_cash_buffer_clip_floors_below_an_off_grid_room() -> None:
    price = Decimal("10.0001")
    qty = Decimal("10.000001")
    value = qty * price
    commission = Decimal("0.0003")
    cash = (Decimal("17.00026") + value / Decimal("10") + commission) / Decimal("0.9")
    book = PortfolioSnapshot(cash, (PositionSnapshot("MSFT", qty, Decimal("10")),))
    settings = _settings(COMMISSION_USD=commission)
    orders, dropped = _run(
        [_proposal("AAPL", "buy", "50")],
        book,
        {"AAPL": Decimal("10.0001"), "MSFT": price},
        settings=settings,
    )
    equity = cash + value
    buffer = equity * Decimal(int(RISK_LIMITS["balanced"]["min_cash_buffer_pct"])) / _HUNDRED
    order_cap = equity * Decimal(int(RISK_LIMITS["balanced"]["max_single_order_pct"])) / _HUNDRED
    room = cash - buffer - commission
    assert Decimal("5") <= room < order_cap
    assert dropped == ()
    assert orders[0].notional_usd == _floor(room)
    assert orders[0].notional_usd is not None
    assert orders[0].notional_usd <= room
    assert cash - orders[0].notional_usd - commission >= buffer
    assert orders[0].clip_reason == "clipped: cash buffer"


def test_full_numeric_width_never_clips_above_the_cap_or_the_shares_held() -> None:
    price = Decimal("9999999999.9999")
    qty = Decimal("999999999999.999999")
    value = qty * price
    book = PortfolioSnapshot(value, (PositionSnapshot("MSFT", qty, price),))
    prices = {"MSFT": price, "AAPL": price}
    orders, dropped = _run(
        [_proposal("MSFT", "sell", "100"), _proposal("AAPL", "buy", "50")],
        book,
        prices,
        level="conservative",
    )
    equity = value + value
    order_cap = equity * Decimal(int(RISK_LIMITS["conservative"]["max_single_order_pct"])) / _HUNDRED
    buffer = equity * Decimal(int(RISK_LIMITS["conservative"]["min_cash_buffer_pct"])) / _HUNDRED
    position_cap = equity * Decimal(int(RISK_LIMITS["conservative"]["max_position_pct"])) / _HUNDRED
    sells = [order for order in orders if order.side == "sell"]
    buys = [order for order in orders if order.side == "buy"]
    assert dropped == ()
    assert sells[0].qty == qty
    assert sells[0].qty is not None and sells[0].qty <= qty
    assert len(buys) == 1
    assert buys[0].notional_usd == _floor(order_cap)
    assert buys[0].notional_usd is not None
    assert buys[0].notional_usd <= order_cap
    assert buys[0].notional_usd <= position_cap
    assert value - buys[0].notional_usd >= buffer

    again, dropped = _run(
        [_proposal("MSFT", "sell", "60"), _proposal("MSFT", "sell", "60")],
        book,
        prices,
        level="conservative",
    )
    assert sum(order.qty or Decimal("0") for order in again) <= qty
    assert dropped == () or dropped[0].reason in {"exceeds held", "clipped: exceeds held", "below $5 minimum"}


def test_cash_zero_and_cash_below_the_buffer_spend_nothing() -> None:
    priced = {"AAPL": Decimal("100"), "MSFT": Decimal("10")}
    broke = PortfolioSnapshot(Decimal("0"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("100")),))
    orders, dropped = _run(
        [_proposal("AAPL", "sell", "100"), _proposal("MSFT", "buy", "5")],
        broke,
        priced,
    )
    assert [(order.ticker, order.side, order.qty) for order in orders] == [("AAPL", "sell", Decimal("10"))]
    assert dropped[0].reason == "clipped: cash buffer"
    assert all(order.side != "buy" for order in orders)

    thin = PortfolioSnapshot(Decimal("50"), (PositionSnapshot("MSFT", Decimal("95"), Decimal("10")),))
    orders, dropped = _run(
        [_proposal("MSFT", "sell", "10"), _proposal("AAPL", "buy", "5")],
        thin,
        {"MSFT": Decimal("10"), "AAPL": Decimal("10")},
    )
    assert [order.side for order in orders] == ["sell"]
    assert orders[0].qty == Decimal("9.5")
    assert dropped[0].reason == "clipped: cash buffer"


def test_underwater_positions_use_the_latest_price_and_do_not_auto_sell() -> None:
    book = PortfolioSnapshot(
        Decimal("100"),
        (
            PositionSnapshot("AAPL", Decimal("1"), Decimal("80")),
            PositionSnapshot("MSFT", Decimal("1"), Decimal("80")),
            PositionSnapshot("NVDA", Decimal("1"), Decimal("80")),
        ),
    )
    prices = {"AAPL": Decimal("1"), "MSFT": Decimal("1"), "NVDA": Decimal("1")}
    quiet, dropped = _run([], book, prices, level="conservative")
    assert quiet == ()
    assert dropped == ()
    stops = check_stop_losses(book.positions, prices, "conservative")
    assert [order.ticker for order in stops] == ["AAPL", "MSFT", "NVDA"]

    marked = PortfolioSnapshot(Decimal("950"), (PositionSnapshot("AAPL", Decimal("10"), Decimal("100")),))
    orders, dropped = _run(
        [_proposal("AAPL", "buy", "15")],
        marked,
        {"AAPL": Decimal("5")},
    )
    assert dropped == ()
    assert orders[0].notional_usd == Decimal("150")
    assert orders[0].clip_reason is None


def test_one_huge_position_blocks_only_that_ticker() -> None:
    book = PortfolioSnapshot(Decimal("400"), (PositionSnapshot("AAPL", Decimal("60"), Decimal("1")),))
    orders, dropped = _run(
        [_proposal("AAPL", "buy", "10", "0.4"), _proposal("MSFT", "buy", "10", "0.9")],
        book,
        {"AAPL": Decimal("10"), "MSFT": Decimal("10")},
    )
    assert [order.ticker for order in orders] == ["MSFT"]
    assert orders[0].notional_usd == Decimal("100")
    assert dropped[0].ticker == "AAPL"
    assert dropped[0].reason == "clipped: max position 25%"


def test_fifty_mixed_proposals_keep_every_limit_and_are_deterministic() -> None:
    book = PortfolioSnapshot(
        Decimal("800"),
        (
            PositionSnapshot("AAPL", Decimal("5"), Decimal("10")),
            PositionSnapshot("MSFT", Decimal("8"), Decimal("20")),
            PositionSnapshot("NVDA", Decimal("10"), Decimal("20")),
            PositionSnapshot("XOM", Decimal("4"), Decimal("20")),
        ),
    )
    prices = {ticker: Decimal("10") for ticker in INTEREST}
    prices.update({"AAPL": Decimal("10"), "MSFT": Decimal("20"), "NVDA": Decimal("20"), "XOM": Decimal("20"), "ZZZZ": Decimal("10")})
    proposals: list[Proposal] = []
    for confidence in ("0.90", "0.80", "0.70", "0.60", "0.40", "0.30", "0.20", "0.10"):
        proposals.append(_proposal("AAPL", "buy", "1", confidence))
    proposals.append(_proposal("GOOGL", "buy", "1", "0.99"))
    proposals.extend(_proposal("MSFT", "sell", "25", "0.2") for _ in range(4))
    proposals.extend(_proposal("MSFT", "buy", "5", "0.95") for _ in range(4))
    proposals.extend(_proposal("NVDA", "sell", "30", "0.3") for _ in range(4))
    proposals.extend(_proposal("XOM", "buy", "5", "0.4") for _ in range(3))
    proposals.extend(_proposal("ZZZZ", "buy", "5", "0.4") for _ in range(3))
    proposals.extend(_proposal("CAT", "buy", "5", "0.4") for _ in range(3))
    proposals.append(_proposal("AAPL", "buy", "0", "0.5"))
    proposals.append(_proposal("AAPL", "sell", "100.01", "0.5"))
    proposals.append(_proposal("AAPL", "hold", "10", "0.5"))
    for ticker, confidence in (
        ("AMZN", "0.50"),
        ("META", "0.50"),
        ("TSLA", "0.50"),
        ("WMT", "0.49"),
        ("COST", "0.48"),
        ("JPM", "0.47"),
        ("BAC", "0.46"),
        ("GS", "0.45"),
        ("UNH", "0.44"),
        ("JNJ", "0.43"),
    ):
        proposals.append(_proposal(ticker, "buy", "1", confidence))
    proposals.extend(_proposal("AVGO", "buy", "1", "0.20") for _ in range(7))
    assert len(proposals) == 50

    interest = tuple(ticker for ticker in INTEREST if ticker != "CAT")
    exclusions = Exclusions(sectors=frozenset({"Energy"}))
    first = _run(proposals, book, prices, interest=interest, exclusions=exclusions)
    second = _run(proposals, book, prices, interest=interest, exclusions=exclusions)
    assert first == second
    orders, dropped = first
    assert Counter((item.ticker, item.action) for item in proposals) == Counter(
        [(order.ticker, order.side) for order in orders] + [(item.ticker, item.action) for item in dropped]
    )

    equity = Decimal("1290")
    buffer = equity * Decimal(int(RISK_LIMITS["balanced"]["min_cash_buffer_pct"])) / _HUNDRED
    order_cap = equity * Decimal(int(RISK_LIMITS["balanced"]["max_single_order_pct"])) / _HUNDRED
    position_cap = equity * Decimal(int(RISK_LIMITS["balanced"]["max_position_pct"])) / _HUNDRED
    buys = [order for order in orders if order.side == "buy"]
    sells = [order for order in orders if order.side == "sell"]
    # The invalid AAPL sell still conflicts, so those buys are not the ones that get the cash.
    assert [order.ticker for order in buys] == ["GOOGL", "AMZN", "META", "TSLA"]
    assert [order.notional_usd for order in buys] == [Decimal("12.90")] * 4
    assert len(buys) == int(RISK_LIMITS["balanced"]["max_new_buys_per_day"])
    assert len({order.ticker for order in buys} | {"AAPL", "MSFT", "NVDA", "XOM"}) == int(
        RISK_LIMITS["balanced"]["max_positions"]
    )
    spent = sum(order.notional_usd or Decimal("0") for order in buys)
    assert book.cash - spent >= buffer
    for order in buys:
        assert order.notional_usd is not None and order.notional_usd <= order_cap
        assert order.ticker not in {"AAPL", "MSFT", "XOM", "ZZZZ", "CAT"}
    assert sum(order.notional_usd or Decimal("0") for order in buys if order.ticker == "AAPL") == 0
    assert Decimal("50") <= position_cap
    sold: dict[str, Decimal] = {}
    for order in sells:
        sold[order.ticker] = sold.get(order.ticker, Decimal("0")) + (order.qty or Decimal("0"))
    assert sold["MSFT"] == Decimal("8")
    assert sold["NVDA"] == Decimal("10")
    assert sells[-1].clip_reason == "clipped: exceeds held"


@pytest.mark.parametrize(
    ("level", "average"),
    [
        ("conservative", Decimal("0.0000001")),
        ("balanced", Decimal("9999999999.9999")),
        ("aggressive", Decimal("1234567890123.123456789")),
    ],
)
def test_stop_loss_sells_at_the_exact_threshold_and_not_above_it(level: str, average: Decimal) -> None:
    stop = int(RISK_LIMITS[level]["stop_loss_pct"])
    threshold = average * (Decimal(100 + stop) / _HUNDRED)
    assert threshold > 0
    qty = Decimal("999999999999.999999")
    position = PositionSnapshot("AAPL", qty, average)
    at_stop = check_stop_losses([position], {"AAPL": threshold}, level)
    assert len(at_stop) == 1
    assert at_stop[0].qty == qty
    assert at_stop[0].system is True
    assert at_stop[0].summary == f"Stop-loss at {stop}%"
    above = threshold + Decimal("1E-10")
    assert above > threshold
    assert check_stop_losses([position], {"AAPL": above}, level) == ()


def test_player_exact_held_exact_cash_and_the_five_dollar_line() -> None:
    qty = Decimal("123456.123456")
    price = Decimal("10.0001")
    held = PortfolioSnapshot(Decimal("0"), (PositionSnapshot("AAPL", qty, Decimal("10")),))
    exact_qty = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=qty),
        held,
        {"AAPL": price},
        UNIVERSE,
        _settings(),
    )
    assert isinstance(exact_qty, RiskOrder)
    assert exact_qty.qty == qty
    over_qty = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=qty + Decimal("0.000001")),
        held,
        {"AAPL": price},
        UNIVERSE,
        _settings(),
    )
    assert isinstance(over_qty, Dropped)
    assert over_qty.reason == "exceeds held"

    wide = PortfolioSnapshot(
        Decimal("0"),
        (PositionSnapshot("AAPL", Decimal("999999999999.999999"), Decimal("1")),),
    )
    wide_qty = Decimal("999999999999.999999")
    wide_sell = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=wide_qty),
        wide,
        {"AAPL": Decimal("0.0001")},
        UNIVERSE,
        _settings(),
    )
    assert isinstance(wide_sell, RiskOrder)
    assert wide_sell.qty == wide_qty

    commission = Decimal("0.0001")
    cash = Decimal("100.0001")
    settings = _settings(COMMISSION_USD=commission)
    book = PortfolioSnapshot(cash)
    prices = {"AAPL": Decimal("10.0001")}
    exact_cash = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=cash - commission),
        book,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(exact_cash, RiskOrder)
    assert exact_cash.notional_usd == Decimal("100.0000")
    over_cash = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=cash - commission + Decimal("0.0001")),
        book,
        prices,
        UNIVERSE,
        settings,
    )
    assert isinstance(over_cash, Dropped)
    assert over_cash.reason == "insufficient cash"

    small = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("4.99")),
        PortfolioSnapshot(Decimal("100")),
        prices,
        UNIVERSE,
        _settings(),
    )
    assert isinstance(small, Dropped)
    assert small.reason == "below $5 minimum"
    five = validate_player_order(
        PlayerOrder("AAPL", "buy", notional_usd=Decimal("5.00")),
        PortfolioSnapshot(Decimal("5.00")),
        prices,
        UNIVERSE,
        _settings(),
    )
    assert isinstance(five, RiskOrder)
    assert five.notional_usd == Decimal("5.00")

    cheap_sell = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=Decimal("1")),
        PortfolioSnapshot(Decimal("0"), (PositionSnapshot("AAPL", Decimal("1"), Decimal("10")),)),
        {"AAPL": Decimal("4.99")},
        UNIVERSE,
        _settings(),
    )
    assert isinstance(cheap_sell, Dropped)
    assert cheap_sell.reason == "below $5 minimum"
    five_sell = validate_player_order(
        PlayerOrder("AAPL", "sell", qty=Decimal("1.000000")),
        PortfolioSnapshot(Decimal("0"), (PositionSnapshot("AAPL", Decimal("1.000000"), Decimal("10")),)),
        {"AAPL": Decimal("5.00")},
        UNIVERSE,
        _settings(),
    )
    assert isinstance(five_sell, RiskOrder)
    assert five_sell.qty == Decimal("1.000000")
