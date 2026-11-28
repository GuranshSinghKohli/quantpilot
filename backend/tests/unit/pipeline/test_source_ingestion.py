"""IngestionRunner failure handling, the adapter registry, configuration, windows,
source health and the CLI. Mock providers and fake Kafka only."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from confluent_kafka import KafkaError

from app.domain.enums import IngestionRecordStatus as S
from app.domain.enums import IngestionRunStatus as R
from app.pipeline.contracts import SOURCES
from app.pipeline.sources.registry import REGISTRY, ConfigurationError, Wiring, build_adapter, check_health
from app.pipeline.sources.window import parse_bound, resolve_window
from tests.unit.pipeline.pipeline_env import DLQ_TOPIC
from tests.unit.pipeline.source_harness import (  # noqa: F401  (source_env is a fixture)
    NEWS_PATH,
    SEC_PATH,
    MockProvider,
    agg_path,
    aggs_body,
    article,
    news_body,
    ok,
    request,
    source_env,
    source_settings,
    status,
)


# --- runner failure handling -------------------------------------------------------


def test_crash_mid_run_leaves_a_failed_run_row(source_env):
    adapter = source_env.adapter("news")

    def explode(item, ingested_at):
        raise RuntimeError("simulated bug in normalize")

    adapter.normalize = explode
    source_env.provider.on(NEWS_PATH, ok(news_body([article(1)])))
    runner = source_env.runner(adapter)
    with pytest.raises(RuntimeError, match="simulated bug"):
        runner.run(request())
    (row,) = [r for r in _runs(source_env)]
    assert row.status == R.FAILED.value and "simulated bug" in row.error and row.finished_at is not None


def test_dlq_outage_for_rejects_is_recorded_on_the_run(source_env):
    source_env.fake.behaviour = lambda topic, key, attempt: (
        KafkaError(KafkaError._MSG_TIMED_OUT) if topic == DLQ_TOPIC else None)
    source_env.provider.on(NEWS_PATH, ok(news_body([article(1), {"id": "broken"}])))
    result = source_env.run("news")
    assert (result.status, result.invalid, result.dead_lettered_invalid) == (R.PARTIAL, 1, 0)
    assert "could not be dead-lettered" in source_env.run_row(result.run_id).error


def test_kafka_outage_marks_publish_failed_and_the_next_run_retries(source_env):
    source_env.fake.behaviour = lambda topic, key, attempt: KafkaError(KafkaError._MSG_TIMED_OUT)
    source_env.provider.on(NEWS_PATH, ok(news_body([article(1), article(2)])))
    down = source_env.run("news")
    assert (down.status, down.publish_failed, down.published) == (R.FAILED, 2, 0)
    assert {r.status for r in source_env.records()} == {S.PUBLISH_FAILED.value}
    source_env.fake.behaviour = lambda topic, key, attempt: None
    up = source_env.run("news")
    assert (up.status, up.published, up.duplicate) == (R.COMPLETED, 2, 0)
    assert {r.status for r in source_env.records()} == {S.PUBLISHED.value}


def test_run_counters_satisfy_tracking_invariants_on_mixed_input(source_env):
    shared = article(1)
    source_env.provider.on(NEWS_PATH, ok(news_body([shared, shared, article(2), {"id": "bad"}])))
    result = source_env.run("news")
    row = source_env.run_row(result.run_id)
    assert row.records_received == row.records_valid + row.records_invalid == 4
    assert row.records_valid == row.records_published + row.records_duplicate + row.records_publish_failed == 3
    assert (row.records_duplicate, row.status) == (1, R.PARTIAL.value)


def _runs(env):
    from sqlalchemy import select

    from app.domain.ingestion_models import IngestionRun

    with env.pipeline.sessions() as db:
        return list(db.scalars(select(IngestionRun)))


# --- registry and configuration ------------------------------------------------------


def test_registry_is_explicit_and_real_sources_are_not_synthetic():
    assert set(REGISTRY) == {("news", "massive"), ("market", "massive"), ("company", "sec")}
    for entry in REGISTRY.values():
        spec = SOURCES[entry.source]
        assert spec.domain.value == entry.domain and spec.provider == entry.provider and not spec.synthetic
    assert SOURCES["synthetic.market"].synthetic
    assert "synthetic.market" not in {e.source for e in REGISTRY.values()}


@pytest.mark.parametrize("domain,missing", [("news", "NEWS_API_KEY"), ("market", "MARKET_API_KEY"),
                                            ("company", "SEC_EDGAR_USER_AGENT")])
def test_enabled_adapter_without_credentials_fails_clearly(domain, missing):
    settings = source_settings(**{missing: ""})
    with pytest.raises(ConfigurationError, match=missing):
        build_adapter(domain, settings)


def test_sec_user_agent_must_carry_a_real_contact():
    with pytest.raises(ConfigurationError, match="contact email"):
        build_adapter("company", source_settings(SEC_EDGAR_USER_AGENT="QuantPilot/1.0 (contact@example.com)"))


def test_settings_load_without_any_provider_credentials(monkeypatch):
    from app.core.settings import Settings

    for name in ("NEWS_API_KEY", "MARKET_API_KEY", "SEC_EDGAR_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert not s.NEWS_API_KEY.get_secret_value() and not s.MARKET_API_KEY.get_secret_value()


@pytest.mark.parametrize("url,ok_", [("https://api.massive.com", True), ("http://mock-provider", True),
                                     ("http://localhost:8080/base", True), ("http://api.massive.com", False),
                                     ("ftp://x", False), ("https://h?apiKey=1", False)])
def test_source_base_urls_require_https_off_localhost(url, ok_):
    from pydantic import ValidationError

    if ok_:
        assert source_settings(NEWS_BASE_URL=url).NEWS_BASE_URL == url.rstrip("/")
    else:
        with pytest.raises(ValidationError):
            source_settings(NEWS_BASE_URL=url)


def test_api_key_is_secret_in_settings_repr():
    s = source_settings(NEWS_API_KEY="placeholder-not-a-key")
    assert "placeholder-not-a-key" not in repr(s)


def test_api_process_does_not_import_source_adapters():
    code = "import sys, app.main; print(any(m.startswith('app.pipeline.sources') for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=str(__import__('pathlib').Path(__file__).resolve().parents[3]))
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"


# --- windows --------------------------------------------------------------------------

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def test_window_defaults_to_lookback_ending_now():
    assert resolve_window(None, None, lookback_days=7, max_window_days=366, now=NOW) == (NOW - timedelta(days=7), NOW)


def test_window_date_bounds_cover_whole_days():
    start, end = resolve_window("2026-09-01", "2026-09-05", lookback_days=7, max_window_days=366, now=NOW)
    assert (start.isoformat(), end.isoformat()) == ("2026-09-01T00:00:00+00:00", "2026-09-05T23:59:59+00:00")
    assert parse_bound("2026-09-01T10:00:00+02:00", end=False) == datetime(2026, 9, 1, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("start,end,match", [
    ("2026-09-05", "2026-09-01", "not before"),
    ("2020-01-01", "2026-09-01", "INGEST_MAX_WINDOW_DAYS"),
    ("2026-09-01", "2027-01-01", "future"),
    ("2026-09-01T10:00:00", None, "offset"),
    ("yesterday", None, "not a date"),
])
def test_window_bounds_are_enforced(start, end, match):
    with pytest.raises(ConfigurationError, match=match):
        resolve_window(start, end, lookback_days=7, max_window_days=366, now=NOW)


# --- source health --------------------------------------------------------------------


def _health(domain, provider, **settings):
    return check_health(domain, source_settings(**settings), Wiring(transport=provider.transport(),
                                                                  sleep=lambda s: None))


def test_unconfigured_source_reports_without_any_request():
    provider = MockProvider()
    report = _health("news", provider, NEWS_API_KEY="")
    assert (report.configured, report.healthy, report.missing) == (False, False, ["NEWS_API_KEY is not set"])
    assert provider.hits() == 0


def test_health_reports_auth_rate_limit_headers_and_reachability():
    provider = MockProvider().on(NEWS_PATH, ok(news_body([]), headers={"X-RateLimit-Remaining": "4"}))
    good = _health("news", provider)
    assert (good.reachable, good.auth, good.healthy, good.rate_limit) == (True, "valid", True,
                                                                          {"x-ratelimit-remaining": "4"})
    assert provider.requests[0].url.params["limit"] == "1"
    bad = _health("news", MockProvider().on(NEWS_PATH, status(401)))
    assert (bad.auth, bad.healthy) == ("invalid", False)
    limited = _health("news", MockProvider().on(NEWS_PATH, status(429)))
    assert limited.auth == "unknown" and "rate limited" in limited.error


def test_health_is_never_retried_and_reports_unreachable():
    provider = MockProvider().on(NEWS_PATH, httpx.ConnectError("simulated refused"))
    report = _health("news", provider)
    assert (report.reachable, report.healthy) == (False, False) and provider.hits() == 1


def test_sec_health_uses_head_and_needs_no_key():
    provider = MockProvider().on(SEC_PATH, httpx.Response(200))
    report = _health("company", provider)
    assert (report.auth, report.healthy) == ("not_required", True)
    assert provider.requests[0].method == "HEAD"


def test_market_health_uses_a_short_bar_request_for_the_first_symbol():
    today = datetime.now(timezone.utc).date()
    path = agg_path("MSFT", (today - timedelta(days=7)).isoformat(), today.isoformat())
    provider = MockProvider().on(path, ok(aggs_body("MSFT", [])))
    report = _health("market", provider, MARKET_SYMBOLS="MSFT,AAPL")
    assert report.healthy and provider.hits(path) == 1


# --- CLI -----------------------------------------------------------------------------


def test_cli_ingest_without_credentials_exits_2_with_the_variable_name(capsys):
    from app.pipeline.__main__ import main

    assert main(["ingest", "news"]) == 2
    assert "NEWS_API_KEY" in capsys.readouterr().err


def test_cli_rejects_unbounded_or_invalid_requests(capsys):
    from app.pipeline.__main__ import main

    assert main(["ingest", "market", "--max-records", "1000000"]) == 2
    assert main(["ingest", "company", "--start", "2020-01-01", "--end", "2026-09-01"]) == 2
    assert main(["ingest", "news", "--all"]) == 2
    err = capsys.readouterr().err
    assert "--max-records" in err and "INGEST_MAX_WINDOW_DAYS" in err and "--all" in err


def test_cli_sources_check_reports_unconfigured_sources(capsys):
    from app.pipeline.__main__ import main

    assert main(["sources", "check", "news", "market"]) == 0
    reports = json.loads(capsys.readouterr().out)["sources"]
    assert [(r["source"], r["configured"]) for r in reports] == [("massive.news", False), ("massive.market_daily", False)]
