"""Mock providers and wiring for source adapter tests. No real provider is ever
contacted: every adapter gets an ``httpx.MockTransport`` and a fake clock.

Response builders follow the shape of the documented Massive samples and the
real SEC document (see tests/fixtures/sources/README.md); their values are
test values, named as such.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.core.settings import Settings
from app.domain.ingestion_models import IngestionRecord, IngestionRun
from app.pipeline.ingest import IngestionRunner
from app.pipeline.kafka.producer import DeadLetterPublisher, EventProducer
from app.pipeline.lake import LocalObjectStore
from app.pipeline.metrics import Metrics
from app.pipeline.serde import decode_dead_letter
from app.pipeline.sources.base import IngestRequest
from app.pipeline.sources.registry import Wiring, build_adapter
from tests.unit.pipeline.kafka_fakes import FakeProducer
from tests.unit.pipeline.pipeline_env import DLQ_TOPIC, MAX_BYTES, POLICY, TOPICS
from tests.unit.pipeline.pipeline_env import Wiring as PipelineWiring
from tests.unit.pipeline.pipeline_env import migrated_sessions

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sources"
BASE = "http://mock-provider"
TEST_KEY = "test-key-not-a-real-credential"
AGENT = "QuantPilot tests qa@quantpilot.test"
NEWS_PATH = "/v2/reference/news"
SEC_PATH = "/files/company_tickers_exchange.json"
T = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)  # fixed ingestion time
ET = ZoneInfo("America/New_York")


def fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class FakeClock:
    """Monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class MockProvider:
    """Routes by URL path. Each route is a queue of responses consumed in order;
    the last one repeats. An entry may be an httpx.Response, an exception to
    raise (e.g. httpx.ReadTimeout), or a callable(request) returning either."""

    def __init__(self) -> None:
        self.routes: Dict[str, list] = {}
        self.requests: List[httpx.Request] = []

    def on(self, path: str, *responses) -> "MockProvider":
        self.routes[path] = list(responses)
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(request.url.path)
        if queue is None:
            return httpx.Response(404, json={"status": "NOT_FOUND", "error": f"no mock route {request.url.path}"})
        entry = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(entry) and not isinstance(entry, (httpx.Response, BaseException)):
            entry = entry(request)
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def hits(self, path: Optional[str] = None) -> int:
        return sum(1 for r in self.requests if path is None or r.url.path == path)


def ok(body: Any, headers: Optional[dict] = None) -> httpx.Response:
    return httpx.Response(200, json=body, headers=headers)


def status(code: int, body: Any = None, headers: Optional[dict] = None) -> httpx.Response:
    return httpx.Response(code, json=body if body is not None else {"status": "ERROR", "error": f"test {code}"},
                          headers=headers)


def source_settings(**overrides) -> Settings:
    base = dict(
        NEWS_API_KEY=TEST_KEY, MARKET_API_KEY=TEST_KEY, SEC_EDGAR_USER_AGENT=AGENT,
        NEWS_BASE_URL=BASE, MARKET_BASE_URL=BASE, COMPANY_BASE_URL=BASE,
        NEWS_REQUESTS_PER_MINUTE=60, MARKET_REQUESTS_PER_MINUTE=60, COMPANY_REQUESTS_PER_MINUTE=600,
        PIPELINE_RETRY_MAX_ATTEMPTS=3, PIPELINE_RETRY_BASE_SECONDS=0.5, PIPELINE_RETRY_MAX_SECONDS=2,
        SOURCE_MAX_RETRY_AFTER_SECONDS=30,
    )
    return Settings(**{**base, **overrides})


def request(start="2026-09-01", end="2026-09-05", symbols=("AAPL",), **limits) -> IngestRequest:
    return IngestRequest(
        start=datetime.combine(date.fromisoformat(start), time(0), timezone.utc),
        end=datetime.combine(date.fromisoformat(end), time(23, 59, 59), timezone.utc),
        symbols=tuple(symbols), **limits,
    )


# --- Massive news -------------------------------------------------------------------


def article(n: int, **over) -> Dict[str, Any]:
    """Test article shaped like the documented sample, with test values."""
    a = copy.deepcopy(fixture("massive_news.sample.json")["results"][0])
    a.update(
        id=f"test-article-{n:04d}",
        article_url=f"https://news.test/articles/{n}",
        title=f"Test article {n}",
        published_utc=(datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        tickers=["AAPL"],
    )
    a.update(over)
    return a


def news_body(articles: List[dict], next_url: Optional[str] = None) -> Dict[str, Any]:
    body = {"status": "OK", "request_id": "test-request", "count": len(articles), "results": articles}
    if next_url:
        body["next_url"] = next_url
    return body


def next_news(cursor: str) -> str:
    return f"{BASE}{NEWS_PATH}?cursor={cursor}"


# --- Massive aggregates -------------------------------------------------------------


def agg_path(symbol="AAPL", start="2026-09-01", end="2026-09-05") -> str:
    return f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"


def bar(day: date, price: float = 100.0, **over) -> Dict[str, Any]:
    start = datetime.combine(day, time(0), ET)  # Massive day bars start at 00:00 ET
    b = {"o": price, "h": price + 2, "l": price - 1, "c": price + 1, "v": 1000 + day.day, "vw": price + 0.5,
         "n": 42, "t": int(start.timestamp() * 1000)}
    b.update(over)
    return b


def aggs_body(ticker: str, bars: List[dict], next_url: Optional[str] = None) -> Dict[str, Any]:
    body = {"ticker": ticker, "adjusted": False, "queryCount": len(bars), "resultsCount": len(bars),
            "request_id": "test-request", "status": "OK", "results": bars}
    if next_url:
        body["next_url"] = next_url
    return body


def weekdays(start: date, count: int) -> List[date]:
    out, day = [], start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


# --- SEC ------------------------------------------------------------------------------


def sec_body(rows: Optional[List[list]] = None) -> Dict[str, Any]:
    real = fixture("sec_company_tickers_exchange.subset.json")
    return {"fields": real["fields"], "data": real["data"] if rows is None else rows}


# --- wiring ---------------------------------------------------------------------------


@dataclass
class SourceEnv:
    pipeline: PipelineWiring
    fake: FakeProducer
    provider: MockProvider
    clock: FakeClock
    metrics: Metrics

    def adapter(self, domain: str, **settings_overrides):
        return build_adapter(domain, source_settings(**settings_overrides),
                             Wiring(transport=self.provider.transport(), sleep=self.clock.sleep, clock=self.clock,
                                    metrics=self.metrics))

    def runner(self, adapter) -> IngestionRunner:
        return IngestionRunner(adapter=adapter, publisher=self.pipeline.publisher(),
                               dlq=DeadLetterPublisher(self.pipeline.producer, DLQ_TOPIC),
                               session_factory=self.pipeline.sessions, max_bytes=MAX_BYTES, metrics=self.metrics,
                               trigger="test", now=lambda: T)

    def run(self, domain: str, req: Optional[IngestRequest] = None, **settings_overrides):
        adapter = self.adapter(domain, **settings_overrides)
        try:
            return self.runner(adapter).run(req or request())
        finally:
            adapter.close()

    def records(self, run_id=None) -> List[IngestionRecord]:
        with self.pipeline.sessions() as db:
            query = select(IngestionRecord)
            if run_id is not None:
                query = query.where(IngestionRecord.run_id == run_id)
            return list(db.scalars(query))

    def run_row(self, run_id) -> IngestionRun:
        import uuid

        with self.pipeline.sessions() as db:
            return db.get(IngestionRun, uuid.UUID(str(run_id)))

    def published(self, domain: str):
        from app.pipeline.serde import decode_event

        return [decode_event(m.value(), MAX_BYTES) for m in self.fake.on(TOPICS[domain])]

    def dead_letters(self):
        return [decode_dead_letter(m.value()) for m in self.fake.on(DLQ_TOPIC)]


@pytest.fixture()
def source_env(tmp_path):
    fake, metrics, clock = FakeProducer(), Metrics(), FakeClock()
    producer = EventProducer({}, policy=POLICY, metrics=metrics, producer=fake, sleep=clock.sleep)
    pipeline = PipelineWiring(migrated_sessions(tmp_path / "s.db"), LocalObjectStore(tmp_path / "lake"), producer,
                              metrics, TOPICS, DLQ_TOPIC, clock.sleeps)
    return SourceEnv(pipeline, fake, MockProvider(), clock, metrics)
