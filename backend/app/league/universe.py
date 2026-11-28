"""Season universe loaded from a CSV. No prices and no cash."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

_COLUMNS = ("ticker", "name", "sector", "market_cap")
_TICKER = re.compile(r"^[A-Z][A-Z.]{0,5}$")
_REPO_ROOT = Path(__file__).resolve().parents[3]


class UniverseError(Exception):
    """The universe file cannot be loaded."""


class UniverseNotFound(UniverseError):
    """UNIVERSE_PATH does not name a file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"universe file not found: {path}")


class UniverseColumns(UniverseError):
    """The header is not exactly ticker, name, sector, market_cap."""

    def __init__(self, found: tuple[str, ...]) -> None:
        self.found = found
        actual = ", ".join(found) if found else "(none)"
        super().__init__(
            f"universe columns must be {', '.join(_COLUMNS)}; found {actual}"
        )


class InvalidTicker(UniverseError):
    """The ticker is not an uppercase symbol of the allowed shape."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"invalid ticker: {ticker!r}")


class DuplicateTicker(UniverseError):
    """The same ticker appears twice."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"duplicate ticker: {ticker}")


class EmptySector(UniverseError):
    """A listing has no sector."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"empty sector for ticker: {ticker}")


class InvalidMarketCap(UniverseError):
    """market_cap is not empty and not a non-negative decimal."""

    def __init__(self, ticker: str, value: str) -> None:
        self.ticker = ticker
        self.value = value
        super().__init__(f"invalid market_cap for {ticker}: {value!r}")


@dataclass(frozen=True)
class Listing:
    ticker: str
    name: str
    sector: str
    market_cap: Decimal | None


@dataclass(frozen=True)
class Universe:
    """File order is preserved. Lookups do not copy or change case."""

    listings: tuple[Listing, ...]
    _by_ticker: Mapping[str, Listing]
    _by_sector: Mapping[str, tuple[Listing, ...]]

    def get(self, ticker: str) -> Listing:
        try:
            return self._by_ticker[ticker]
        except KeyError:
            raise UniverseTickerNotFound(ticker) from None

    def by_sector(self, sector: str) -> tuple[Listing, ...]:
        return self._by_sector.get(sector, ())


class UniverseTickerNotFound(UniverseError):
    """No listing uses this ticker. Lookup is case-sensitive."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"ticker not in universe: {ticker!r}")


def load_universe(path: str | Path) -> Universe:
    """Read the CSV at path. A relative path is resolved from the repo root."""
    source = Path(path)
    resolved = source if source.is_absolute() else _REPO_ROOT / source
    if not resolved.is_file():
        raise UniverseNotFound(resolved)
    with resolved.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise UniverseColumns(())
    columns = tuple(cell.strip() for cell in rows[0])
    if columns != _COLUMNS:
        raise UniverseColumns(columns)
    listings: list[Listing] = []
    seen: set[str] = set()
    sectors: dict[str, list[Listing]] = {}
    for raw in rows[1:]:
        if not raw or all(not cell.strip() for cell in raw):
            continue
        values = [cell.strip() for cell in raw]
        if len(values) != len(_COLUMNS):
            raise UniverseColumns(tuple(values))
        ticker, name, sector, market_cap = values
        if not _TICKER.fullmatch(ticker) or ticker.endswith("."):
            raise InvalidTicker(ticker)
        if ticker in seen:
            raise DuplicateTicker(ticker)
        if not sector:
            raise EmptySector(ticker)
        parsed = _market_cap(ticker, market_cap)
        listing = Listing(ticker, name, sector, parsed)
        seen.add(ticker)
        listings.append(listing)
        sectors.setdefault(sector, []).append(listing)
    by_ticker = {listing.ticker: listing for listing in listings}
    by_sector = {sector: tuple(items) for sector, items in sectors.items()}
    return Universe(
        tuple(listings),
        MappingProxyType(by_ticker),
        MappingProxyType(by_sector),
    )


def _market_cap(ticker: str, value: str) -> Decimal | None:
    if value == "":
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidMarketCap(ticker, value) from exc
    if not number.is_finite() or number < 0:
        raise InvalidMarketCap(ticker, value)
    return number
