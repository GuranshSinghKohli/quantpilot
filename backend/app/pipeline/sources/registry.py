"""Explicit, typed registry of runnable source adapters.

``(domain, provider)`` → how to build it and which settings it requires. Adding a
provider means one adapter class and one entry here; the runner, publisher,
Kafka, lake writer and repositories do not change. Nothing is imported
dynamically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from app.core.settings import Settings
from app.pipeline.errors import PipelineError
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy
from app.pipeline.sources.base import HealthReport, SourceAdapter
from app.pipeline.sources.http import SourceClient, Throttle
from app.pipeline.sources.massive import MassiveMarketAdapter, MassiveNewsAdapter
from app.pipeline.sources.sec import SecCompanyAdapter

DOMAINS = ("news", "market", "company")


class ConfigurationError(PipelineError):
    """An adapter was requested but its required settings are missing or invalid."""


@dataclass(frozen=True)
class Wiring:
    """Test seams: an httpx transport (mock provider), a clock and a sleep."""

    transport: Optional[httpx.BaseTransport] = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    metrics: Optional[Metrics] = None


@dataclass(frozen=True)
class ProviderEntry:
    domain: str
    provider: str
    source: str
    required: Tuple[str, ...]
    build: Callable[[Settings, Wiring], SourceAdapter]
    check: Callable[[Settings], List[str]]


def _client(settings: Settings, wiring: Wiring, *, provider: str, base_url: str, headers: Dict[str, str],
            per_minute: float) -> SourceClient:
    return SourceClient(
        provider=provider,
        base_url=base_url,
        headers=headers,
        timeout_s=settings.SOURCE_TIMEOUT_SECONDS,
        throttle=Throttle(per_minute, clock=wiring.clock, sleep=wiring.sleep),
        policy=RetryPolicy.from_settings(settings),
        max_retry_after_s=settings.SOURCE_MAX_RETRY_AFTER_SECONDS,
        metrics=wiring.metrics,
        transport=wiring.transport,
        sleep=wiring.sleep,
    )


def _bearer(key) -> Dict[str, str]:
    return {"Authorization": f"Bearer {key.get_secret_value()}", "Accept": "application/json"}


def _require_secret(name: str):
    def check(settings: Settings) -> List[str]:
        return [] if getattr(settings, name).get_secret_value() else [f"{name} is not set"]
    return check


def _check_sec(settings: Settings) -> List[str]:
    agent = settings.SEC_EDGAR_USER_AGENT
    if not agent:
        return ["SEC_EDGAR_USER_AGENT is not set (SEC requires 'Name contact@email')"]
    if "@" not in agent or "example.com" in agent:
        return ["SEC_EDGAR_USER_AGENT must carry a real contact email (SEC fair-access policy)"]
    return []


def _massive_news(settings: Settings, wiring: Wiring) -> SourceAdapter:
    return MassiveNewsAdapter(_client(settings, wiring, provider="massive", base_url=settings.NEWS_BASE_URL,
                                      headers=_bearer(settings.NEWS_API_KEY),
                                      per_minute=settings.NEWS_REQUESTS_PER_MINUTE))


def _massive_market(settings: Settings, wiring: Wiring) -> SourceAdapter:
    symbols = Settings.symbol_list(settings.MARKET_SYMBOLS)
    return MassiveMarketAdapter(
        _client(settings, wiring, provider="massive", base_url=settings.MARKET_BASE_URL,
                headers=_bearer(settings.MARKET_API_KEY), per_minute=settings.MARKET_REQUESTS_PER_MINUTE),
        health_symbol=symbols[0] if symbols else "AAPL",
    )


def _sec_company(settings: Settings, wiring: Wiring) -> SourceAdapter:
    return SecCompanyAdapter(_client(settings, wiring, provider="sec", base_url=settings.COMPANY_BASE_URL,
                                     headers={"User-Agent": settings.SEC_EDGAR_USER_AGENT, "Accept": "application/json"},
                                     per_minute=settings.COMPANY_REQUESTS_PER_MINUTE))


REGISTRY: Dict[Tuple[str, str], ProviderEntry] = {
    (e.domain, e.provider): e
    for e in (
        ProviderEntry("news", "massive", "massive.news", ("NEWS_API_KEY",), _massive_news,
                      _require_secret("NEWS_API_KEY")),
        ProviderEntry("market", "massive", "massive.market_daily", ("MARKET_API_KEY",), _massive_market,
                      _require_secret("MARKET_API_KEY")),
        ProviderEntry("company", "sec", "sec.company_tickers", ("SEC_EDGAR_USER_AGENT",), _sec_company, _check_sec),
    )
}


def provider_entry(domain: str, settings: Settings) -> ProviderEntry:
    if domain not in DOMAINS:
        raise ConfigurationError(f"unknown source domain {domain!r}; expected one of {DOMAINS}")
    provider = getattr(settings, f"{domain.upper()}_PROVIDER")
    entry = REGISTRY.get((domain, provider))
    if entry is None:
        raise ConfigurationError(f"no adapter registered for {domain} provider {provider!r}")
    return entry


def missing_config(domain: str, settings: Settings) -> List[str]:
    return provider_entry(domain, settings).check(settings)


def build_adapter(domain: str, settings: Settings, wiring: Wiring = Wiring()) -> SourceAdapter:
    """Fail fast, naming the variable, when a requested adapter is not configured."""
    entry = provider_entry(domain, settings)
    problems = entry.check(settings)
    if problems:
        raise ConfigurationError(f"{domain} source ({entry.provider}) is not configured: " + "; ".join(problems))
    return entry.build(settings, wiring)


def check_health(domain: str, settings: Settings, wiring: Wiring = Wiring()) -> HealthReport:
    entry = provider_entry(domain, settings)
    problems = entry.check(settings)
    if problems:
        return HealthReport(entry.source, entry.provider, configured=False, missing=problems)
    adapter = entry.build(settings, wiring)
    try:
        return adapter.health()
    finally:
        adapter.close()
