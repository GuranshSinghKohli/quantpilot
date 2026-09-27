"""Adapter normalization and fetch behaviour for every source: Massive news,
Massive daily aggregates and SEC company tickers. Mock providers only."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.domain.enums import IngestionRecordStatus as S
from app.domain.enums import IngestionRunStatus as R
from app.pipeline.contracts import SOURCES
from app.pipeline.sources.base import NormalizationError, SourceItem
from app.pipeline.sources.massive import derived_news_id
from app.pipeline.sources.sec import group_by_cik
from tests.unit.pipeline.source_harness import (  # noqa: F401  (source_env is a fixture)
    BASE,
    NEWS_PATH,
    SEC_PATH,
    T,
    agg_path,
    aggs_body,
    article,
    bar,
    fixture,
    news_body,
    next_news,
    ok,
    request,
    sec_body,
    source_env,
    status,
    weekdays,
)

DAYS = weekdays(date(2026, 9, 1), 40)


def company_row(n: int, **over):
    row = [900_000 + n, f"Test Company {n}", f"TST{n}", "Nasdaq"]
    for i, name in enumerate(("cik", "name", "ticker", "exchange")):
        if name in over:
            row[i] = over[name]
    return row


# Per-domain wiring for the scenarios every adapter must handle.
SPECS = {
    "news": dict(path=NEWS_PATH, item=article, body=lambda items, nxt=None: news_body(items, nxt),
                 next=next_news, req=lambda **kw: request(symbols=("AAPL",), **kw),
                 missing=lambda n: {k: v for k, v in article(n).items() if k != "title"}),
    "market": dict(path=agg_path(), item=lambda n: bar(DAYS[n], 100 + n),
                   body=lambda items, nxt=None: aggs_body("AAPL", items, nxt),
                   next=lambda c: f"{BASE}{agg_path()}?cursor={c}", req=lambda **kw: request(symbols=("AAPL",), **kw),
                   missing=lambda n: {k: v for k, v in bar(DAYS[n]).items() if k != "v"}),
    "company": dict(path=SEC_PATH, item=company_row, body=lambda items, nxt=None: sec_body(items), next=None,
                    req=lambda **kw: request(symbols=(), **kw), missing=lambda n: company_row(n, name=None)),
}
ALL = list(SPECS)
PAGINATED = ["news", "market"]


def serve(env, domain, *responses):
    env.provider.on(SPECS[domain]["path"], *responses)


def run(env, domain, **limits):
    return env.run(domain, SPECS[domain]["req"](**limits))


def items(domain, count, start=0):
    return [SPECS[domain]["item"](n) for n in range(start, start + count)]


# --- normalization against the documented / captured fixtures ---------------------


def test_news_sample_normalizes_and_keeps_every_provider_field(source_env):
    adapter = source_env.adapter("news")
    raw = fixture("massive_news.sample.json")["results"][0]
    record = adapter.normalize(SourceItem(raw, raw["id"]), ingested_at=T)
    assert (record.source, record.external_id) == ("massive.news", raw["id"])
    assert record.url == raw["article_url"] and record.title == raw["title"]
    assert record.published_at == datetime(2024, 6, 24, 18, 33, 53, tzinfo=timezone.utc)
    assert (record.publisher, record.author, record.tickers) == ("Investing.com", "Sam Boughedda", ("UBS",))
    assert record.summary.startswith("UBS analysts warn")
    # Raw preservation: build the event and check nothing provider-specific was lost.
    from app.pipeline.contracts import build_event
    import uuid

    event = build_event(record, run_id=uuid.uuid4(), original=raw)
    assert event.original == raw
    assert {"insights", "keywords", "amp_url", "image_url"} <= set(event.original)
    assert event.original["publisher"]["logo_url"].endswith("investing.png")


def test_news_timestamps_normalize_to_utc_and_naive_ones_are_rejected(source_env):
    adapter = source_env.adapter("news")
    offset = adapter.normalize(SourceItem(article(1, published_utc="2026-09-02T16:30:00+02:00"), "a"), ingested_at=T)
    assert offset.published_at == datetime(2026, 9, 2, 14, 30, tzinfo=timezone.utc)
    with pytest.raises(NormalizationError, match="UTC offset"):
        adapter.normalize(SourceItem(article(1, published_utc="2026-09-02T16:30:00"), "a"), ingested_at=T)


def test_news_without_provider_id_gets_a_deterministic_derived_identity(source_env):
    adapter = source_env.adapter("news")
    one = adapter.normalize(SourceItem(article(1, id=None), "a"), ingested_at=T)
    again = adapter.normalize(SourceItem(article(1, id=None), "a"), ingested_at=T)
    other = adapter.normalize(SourceItem(article(2, id=None), "b"), ingested_at=T)
    assert one.external_id == again.external_id == derived_news_id(
        "https://news.test/articles/1", "Investing.com", one.published_at)
    assert one.external_id.startswith("derived:") and one.external_id != other.external_id


def test_news_non_equity_tickers_stay_raw_only(source_env):
    raw = article(1, tickers=["AAPL", "X:BTCUSD", "msft"])
    record = source_env.adapter("news").normalize(SourceItem(raw, "a"), ingested_at=T)
    assert record.tickers == ("AAPL", "MSFT") and raw["tickers"] == ["AAPL", "X:BTCUSD", "msft"]


def test_market_sample_normalizes_to_exchange_session_dates(source_env):
    adapter = source_env.adapter("market")
    sample = fixture("massive_aggs.sample.json")
    records = [adapter.normalize(SourceItem({"ticker": "AAPL", "adjusted": sample["adjusted"], "bar": b}, "x"),
                                 ingested_at=T) for b in sample["results"]]
    first = records[0]
    # t = 1577941200000 = 2020-01-02T05:00Z = midnight EST: session 2020-01-02.
    assert first.timestamp == datetime(2020, 1, 2, 5, 0, tzinfo=timezone.utc)
    assert first.trading_date == date(2020, 1, 2) and records[1].trading_date == date(2020, 1, 3)
    assert (first.open, first.high, first.low, first.close) == (
        Decimal("74.060000"), Decimal("75.150000"), Decimal("73.797500"), Decimal("75.087500"))
    assert first.volume == 135647456
    assert (first.adj_close, first.currency) == (None, None)  # not provided → not guessed


def test_market_summer_bars_use_edt_session_dates(source_env):
    record = source_env.adapter("market").normalize(
        SourceItem({"ticker": "AAPL", "bar": bar(date(2026, 9, 1))}, "x"), ingested_at=T)
    assert record.timestamp == datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)
    assert record.trading_date == date(2026, 9, 1)


@pytest.mark.parametrize("over,field", [({"v": None}, "v"), ({"v": 10.5}, "volume"), ({"t": "yesterday"}, "t"),
                                        ({"l": 500}, None), ({"o": -1}, "open")])
def test_market_bad_bars_are_rejected_not_repaired(source_env, over, field):
    raw = {"ticker": "AAPL", "bar": {k: v for k, v in bar(date(2026, 9, 1), **over).items() if v is not None}}
    with pytest.raises(NormalizationError) as err:
        source_env.adapter("market").normalize(SourceItem(raw, "x"), ingested_at=T)
    if field:
        assert any(field in d["field"] for d in err.value.details)


def test_sec_real_rows_group_by_cik_and_keep_every_row(source_env):
    adapter = source_env.adapter("company")
    found = {i.ref: i for i in group_by_cik(sec_body(), set(), provider="sec")}
    assert set(found) == {"cik:1045810", "cik:320193", "cik:1652044", "cik:789019", "cik:1067983"}
    alphabet = adapter.normalize(found["cik:1652044"], ingested_at=T)
    assert (alphabet.cik, alphabet.external_id, alphabet.name) == ("0001652044", "0001652044", "Alphabet Inc.")
    assert alphabet.tickers == ("GOOGL", "GOOG", "GOOGM", "GOOGN") and alphabet.ticker == "GOOGL"
    assert alphabet.exchange == "Nasdaq"
    assert len(found["cik:1652044"].original["data"]) == 4
    berkshire = adapter.normalize(found["cik:1067983"], ingested_at=T)
    assert berkshire.tickers == ("BRK-B", "BRK-A") and berkshire.exchange == "NYSE"


def test_sec_same_name_different_cik_are_never_merged(source_env):
    rows = [[1, "Same Name Inc", "AAA", "NYSE"], [2, "Same Name Inc", "BBB", "NYSE"]]
    grouped = group_by_cik(sec_body(rows), set(), provider="sec")
    assert [i.ref for i in grouped] == ["cik:1", "cik:2"]


def test_sec_symbol_filter_and_null_exchange(source_env):
    rows = [[1, "One", "ONE", None], [2, "Two", "TWO", "NYSE"]]
    (only,) = group_by_cik(sec_body(rows), {"one"}, provider="sec")
    assert source_env.adapter("company").normalize(only, ingested_at=T).exchange is None


# --- fetch scenarios every adapter must handle ---------------------------------------


@pytest.mark.parametrize("domain", ALL)
def test_valid_response_is_published_and_tracked(source_env, domain):
    serve(source_env, domain, ok(SPECS[domain]["body"](items(domain, 3))))
    result = run(source_env, domain)
    assert result.status == R.COMPLETED
    assert (result.received, result.valid, result.published, result.invalid) == (3, 3, 3, 0)
    row = source_env.run_row(result.run_id)
    assert (row.status, row.records_received, row.records_published) == ("COMPLETED", 3, 3)
    assert row.params["provider"] == SOURCES[result.source].provider
    assert {r.status for r in source_env.records(row.id)} == {S.PUBLISHED.value}
    events = source_env.published(domain)
    assert len(events) == 3 and all(e.source == result.source for e in events)


@pytest.mark.parametrize("domain", ALL)
def test_empty_response_is_a_completed_empty_run(source_env, domain):
    body = SPECS[domain]["body"]([])
    if domain == "news":
        body.pop("results")  # Massive omits `results` when nothing matches
    serve(source_env, domain, ok(body))
    result = run(source_env, domain)
    assert (result.status, result.received, result.published) == (R.COMPLETED, 0, 0)


@pytest.mark.parametrize("domain", ALL)
@pytest.mark.parametrize("malformed", ["not-json", "wrong-shape"])
def test_malformed_response_fails_the_stream_without_retrying(source_env, domain, malformed):
    response = (httpx.Response(200, content=b"<html>maintenance</html>") if malformed == "not-json"
                else ok({"results": "nope"} if domain != "company" else {"fields": ["x"], "data": []}))
    serve(source_env, domain, response)
    result = run(source_env, domain)
    assert result.status == R.FAILED and result.streams[0].error_kind == "bad_response"
    assert source_env.provider.hits() == 1
    assert source_env.run_row(result.run_id).error.startswith(result.streams[0].label)


@pytest.mark.parametrize("domain", ALL)
def test_records_missing_fields_are_dead_lettered_and_counted(source_env, domain):
    good = items(domain, 2)
    serve(source_env, domain, ok(SPECS[domain]["body"](good + [SPECS[domain]["missing"](9)])))
    result = run(source_env, domain)
    assert (result.status, result.received, result.valid, result.invalid) == (R.PARTIAL, 3, 2, 1)
    assert result.dead_lettered_invalid == 1
    (letter,) = source_env.dead_letters()
    assert (letter.component, letter.failure_reason, letter.source) == ("ingestor", "payload_invalid", result.source)
    assert str(letter.run_id) == result.run_id and letter.retry_count == 0
    assert letter.error_details  # which field failed, without the values


@pytest.mark.parametrize("domain", ALL)
def test_duplicate_records_in_a_response_publish_once(source_env, domain):
    one = items(domain, 2)
    serve(source_env, domain, ok(SPECS[domain]["body"](one + [one[0]] if domain != "company" else one + [one[0]])))
    result = run(source_env, domain)
    if domain == "company":
        # SEC rows with the same CIK are one company (grouped before normalization).
        assert (result.received, result.published, result.duplicate) == (2, 2, 0)
    else:
        assert (result.received, result.published, result.duplicate) == (3, 2, 1)
    assert result.status == R.COMPLETED and len(source_env.published(domain)) == 2


@pytest.mark.parametrize("domain", PAGINATED)
def test_pagination_follows_next_url(source_env, domain):
    spec = SPECS[domain]
    serve(source_env, domain, ok(spec["body"](items(domain, 2), spec["next"]("page2"))),
          ok(spec["body"](items(domain, 2, start=2))))
    result = run(source_env, domain)
    assert (result.published, result.streams[0].pages, result.streams[0].truncated) == (4, 2, False)
    second = source_env.provider.requests[1]
    assert second.url.params["cursor"] == "page2" and second.url.host == "mock-provider"


@pytest.mark.parametrize("domain", PAGINATED)
def test_repeated_cursor_stops_pagination_and_keeps_fetched_records(source_env, domain):
    spec = SPECS[domain]
    serve(source_env, domain, ok(spec["body"](items(domain, 1), spec["next"]("same"))),
          ok(spec["body"](items(domain, 1, start=1), spec["next"]("same"))))
    result = run(source_env, domain)
    assert result.status == R.PARTIAL and result.published == 2
    assert result.streams[0].error_kind == "bad_response" and source_env.provider.hits() == 2


@pytest.mark.parametrize("domain", PAGINATED)
def test_max_pages_bounds_a_provider_that_never_stops(source_env, domain):
    spec = SPECS[domain]
    counter = iter(range(1000))
    serve(source_env, domain, lambda req: ok(spec["body"](items(domain, 1, start=next(counter)),
                                                          spec["next"](f"c{next(counter)}"))))
    result = run(source_env, domain, max_pages=3)
    assert source_env.provider.hits() == 3 and result.streams[0].truncated and result.status == R.COMPLETED


@pytest.mark.parametrize("domain", ALL)
def test_max_records_caps_the_run_without_extra_requests(source_env, domain):
    spec = SPECS[domain]
    nxt = spec["next"]("more") if spec["next"] else None
    serve(source_env, domain, ok(spec["body"](items(domain, 5), nxt)))
    result = run(source_env, domain, max_records=3)
    assert (result.received, result.published, result.record_limit_reached) == (3, 3, True)
    assert result.streams[0].truncated and source_env.provider.hits() == 1


@pytest.mark.parametrize("domain", ALL)
def test_timeout_is_retried_then_fails_the_run(source_env, domain):
    serve(source_env, domain, httpx.ReadTimeout("simulated timeout"))
    result = run(source_env, domain)
    assert result.status == R.FAILED and result.streams[0].error_kind == "network_retries_exhausted"
    assert source_env.provider.hits() == 3  # PIPELINE_RETRY_MAX_ATTEMPTS in the test settings


@pytest.mark.parametrize("domain", ALL)
def test_429_honours_retry_after_then_succeeds(source_env, domain):
    serve(source_env, domain, status(429, headers={"Retry-After": "9"}), ok(SPECS[domain]["body"](items(domain, 1))))
    result = run(source_env, domain)
    assert result.status == R.COMPLETED and result.published == 1
    assert 9.0 in source_env.clock.sleeps
    assert source_env.metrics.value("source_rate_limited_total", provider=SOURCES[result.source].provider) == 1


@pytest.mark.parametrize("domain", ALL)
def test_500_is_retried_then_fails(source_env, domain):
    serve(source_env, domain, status(500))
    result = run(source_env, domain)
    assert result.status == R.FAILED and result.streams[0].error_kind == "outage_retries_exhausted"
    assert source_env.provider.hits() == 3


@pytest.mark.parametrize("domain", ALL)
def test_400_fails_once_without_retry(source_env, domain):
    serve(source_env, domain, status(400, {"status": "ERROR", "error": "test: bad parameter"}))
    result = run(source_env, domain)
    assert result.status == R.FAILED and result.streams[0].error_kind == "invalid_request"
    assert source_env.provider.hits() == 1 and "bad parameter" in result.error


@pytest.mark.parametrize("domain", ALL)
@pytest.mark.parametrize("code", [401, 403])
def test_auth_failures_stop_the_whole_run_immediately(source_env, domain, code):
    serve(source_env, domain, status(code))
    spec = SPECS[domain]
    req = spec["req"]() if domain == "company" else request(symbols=("AAPL", "MSFT", "NVDA"))
    source_env.provider.on(agg_path("MSFT"), status(code)).on(agg_path("NVDA"), status(code))
    result = source_env.run(domain, req)
    assert result.status == R.FAILED and result.streams[0].error_kind == "auth"
    assert source_env.provider.hits() == 1 and len(result.streams) == 1


@pytest.mark.parametrize("domain", ALL)
def test_identical_responses_give_identical_event_ids_and_second_run_is_duplicate(source_env, domain):
    body = SPECS[domain]["body"](items(domain, 3))
    serve(source_env, domain, ok(body))
    first = run(source_env, domain)
    second = run(source_env, domain)
    assert (first.published, second.published, second.duplicate) == (3, 0, 3)
    assert second.status == R.COMPLETED
    assert len(source_env.records()) == 3  # no new tracking rows
    assert len(source_env.published(domain)) == 3  # nothing republished


@pytest.mark.parametrize("domain", ALL)
def test_different_records_never_collide(source_env, domain):
    serve(source_env, domain, ok(SPECS[domain]["body"](items(domain, 20))))
    run(source_env, domain)
    ids = [e.event_id for e in source_env.published(domain)]
    assert len(ids) == len(set(ids)) == 20


@pytest.mark.parametrize("domain", PAGINATED)
def test_partial_failure_one_stream_down_others_published(source_env, domain):
    if domain == "news":
        source_env.provider.on(NEWS_PATH, lambda req: ok(news_body([article(1)])) if req.url.params["ticker"] == "AAPL"
                               else status(503))
    else:
        source_env.provider.on(agg_path("AAPL"), ok(aggs_body("AAPL", [bar(DAYS[0])])))
        source_env.provider.on(agg_path("MSFT"), status(503))
    result = source_env.run(domain, request(symbols=("AAPL", "MSFT")))
    assert result.status == R.PARTIAL and result.published == 1
    assert [s.error_kind for s in result.streams] == [None, "outage_retries_exhausted"]
    assert "MSFT: outage_retries_exhausted" in source_env.run_row(result.run_id).error


def test_same_article_under_two_tickers_is_one_event(source_env):
    shared = article(1, tickers=["AAPL", "MSFT"])
    source_env.provider.on(NEWS_PATH, ok(news_body([shared])))
    result = source_env.run("news", request(symbols=("AAPL", "MSFT")))
    assert (result.received, result.published, result.duplicate) == (2, 1, 1)


def test_requests_carry_the_window_and_limits(source_env):
    source_env.provider.on(NEWS_PATH, ok(news_body([])))
    source_env.run("news", request(start="2026-09-01", end="2026-09-05", symbols=("NVDA",), page_size=50))
    params = source_env.provider.requests[0].url.params
    assert (params["ticker"], params["published_utc.gte"], params["published_utc.lte"], params["limit"]) == (
        "NVDA", "2026-09-01T00:00:00Z", "2026-09-05T23:59:59Z", "50")
    source_env.provider.on(agg_path("NVDA"), ok(aggs_body("NVDA", [])))
    source_env.run("market", request(symbols=("NVDA",)))
    market = source_env.provider.requests[-1]
    assert market.url.path == agg_path("NVDA") and market.url.params["adjusted"] == "false"
