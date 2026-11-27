"""Universe loader: columns, tickers, and lookups. No prices."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.league.universe import (
    DuplicateTicker,
    EmptySector,
    InvalidMarketCap,
    InvalidTicker,
    UniverseColumns,
    UniverseNotFound,
    UniverseTickerNotFound,
    load_universe,
)

_FIXTURE = Path("backend/tests/fixtures/universe_small.csv")
_HEADER = "ticker,name,sector,market_cap"


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_fixture_loads_liquid_names_with_empty_market_caps() -> None:
    universe = load_universe(_FIXTURE)
    assert len(universe.listings) == 26
    assert all(listing.market_cap is None for listing in universe.listings)
    apple = universe.get("AAPL")
    assert apple.sector == "Information Technology"
    assert apple.name == "Apple Inc."
    brk = universe.get("BRK.B")
    assert brk.sector == "Financials"
    financials = universe.by_sector("Financials")
    assert [listing.ticker for listing in financials] == ["JPM", "BAC", "GS", "BRK.B"]
    assert [listing.ticker for listing in universe.by_sector("ETF")] == ["SPY", "QQQ", "XLK", "SMH"]
    assert universe.by_sector("Not A Sector") == ()


def test_ticker_lookup_is_case_sensitive() -> None:
    universe = load_universe(_FIXTURE)
    with pytest.raises(UniverseTickerNotFound, match="aapl"):
        universe.get("aapl")


def test_missing_file_names_the_path() -> None:
    missing = Path("config/does-not-exist-universe.csv")
    with pytest.raises(UniverseNotFound, match="does-not-exist-universe.csv"):
        load_universe(missing)


def test_missing_and_extra_columns_name_the_columns(tmp_path: Path) -> None:
    missing = _write(tmp_path / "missing.csv", "ticker,name,sector\nAAPL,Apple,Tech\n")
    with pytest.raises(UniverseColumns, match="ticker, name, sector, market_cap") as missing_info:
        load_universe(missing)
    assert "ticker, name, sector" in str(missing_info.value)
    extra = _write(tmp_path / "extra.csv", f"{_HEADER},note\nAAPL,Apple,Tech,\n")
    with pytest.raises(UniverseColumns, match="note") as extra_info:
        load_universe(extra)
    assert "ticker, name, sector, market_cap" in str(extra_info.value)


def test_bom_and_whitespace_are_stripped(tmp_path: Path) -> None:
    text = " ticker , name , sector , market_cap \n AAPL , Apple Inc. , Information Technology , \n"
    path = tmp_path / "spaced.csv"
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    universe = load_universe(path)
    assert universe.get("AAPL").name == "Apple Inc."
    assert universe.get("AAPL").market_cap is None


def test_bad_tickers_duplicates_and_empty_sectors_are_rejected(tmp_path: Path) -> None:
    for ticker in (".", "A.", "..."):
        path = _write(tmp_path / f"{ticker}.csv", f"{_HEADER}\n{ticker},Name,Financials,\n")
        with pytest.raises(InvalidTicker):
            load_universe(path)
    duplicate = _write(tmp_path / "dup.csv", f"{_HEADER}\nAAPL,Apple,Tech,\n AAPL ,Apple again,Tech,\n")
    with pytest.raises(DuplicateTicker, match="AAPL"):
        load_universe(duplicate)
    blank = _write(tmp_path / "blank.csv", f"{_HEADER}\nAAPL,Apple,  ,\n")
    with pytest.raises(EmptySector, match="AAPL"):
        load_universe(blank)


def test_market_cap_is_kept_when_present_and_rejected_when_negative(tmp_path: Path) -> None:
    present = _write(tmp_path / "cap.csv", f"{_HEADER}\nAAPL,Apple,Tech,1234.5\n")
    assert load_universe(present).get("AAPL").market_cap == Decimal("1234.5")
    zero = _write(tmp_path / "zero.csv", f"{_HEADER}\nAAPL,Apple,Tech,0\n")
    assert load_universe(zero).get("AAPL").market_cap == Decimal("0")
    negative = _write(tmp_path / "neg.csv", f"{_HEADER}\nAAPL,Apple,Tech,-1\n")
    with pytest.raises(InvalidMarketCap, match="AAPL"):
        load_universe(negative)


def test_universe_listings_cannot_be_mutated() -> None:
    universe = load_universe(_FIXTURE)
    with pytest.raises(AttributeError):
        universe.listings[0].ticker = "ZZZ"  # type: ignore[misc]
    with pytest.raises(TypeError):
        universe.by_sector("ETF")[0] = universe.listings[0]  # type: ignore[index]
