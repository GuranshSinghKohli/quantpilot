import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.pipeline.lake import InvalidKey
from app.pipeline.lake.keys import (
    manifest_key,
    processed_prefix,
    raw_key,
    raw_key_for,
    raw_prefix,
    rejects_prefix,
    validate_key,
    validate_prefix,
)
from tests.unit.pipeline.factories import INGESTED_AT, event, news_record

EID = "ab" * 32


def test_raw_key_layout_uses_utc_ingestion_date():
    assert raw_key("market", INGESTED_AT, EID) == f"raw/market/year=2026/month=09/day=02/{EID}.json"
    late_evening_new_york = datetime(2026, 9, 2, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    assert raw_key("news", late_evening_new_york, EID).startswith("raw/news/year=2026/month=09/day=03/")


def test_raw_key_for_event_is_deterministic():
    e = event(news_record())
    assert raw_key_for(e) == raw_key_for(e.model_copy()) == f"raw/news/year=2026/month=09/day=02/{e.event_id}.json"


@pytest.mark.parametrize(
    "args",
    [
        ("orders", INGESTED_AT, EID),  # unknown domain
        ("news", INGESTED_AT.replace(tzinfo=None), EID),  # naive timestamp
        ("news", INGESTED_AT, "AB" * 32),  # not lowercase hex
        ("news", INGESTED_AT, "../../etc/passwd"),
    ],
)
def test_raw_key_rejects_bad_inputs(args):
    with pytest.raises(InvalidKey):
        raw_key(*args)


def test_other_layouts():
    job = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
    assert raw_prefix("company") == "raw/company/"
    assert raw_prefix("company", date(2026, 1, 5)) == "raw/company/year=2026/month=01/day=05/"
    assert processed_prefix("market", date(2026, 9, 1)) == "processed/market/year=2026/month=09/day=01/"
    assert rejects_prefix("news", job) == f"rejects/news/job_id={job}/"
    assert manifest_key(job) == f"_manifests/processing/{job}.json"
    for prefix in (raw_prefix("news"), rejects_prefix("news", job)):
        validate_prefix(prefix)
    validate_key(manifest_key(job))


@pytest.mark.parametrize(
    "key",
    ["", "/raw/x", "raw/x/", "raw//x", "raw/../x", "raw/./x", "raw/.tmp-abc", "raw\\x", "raw/a b", "raw/é", "x" * 1025],
)
def test_invalid_keys(key):
    with pytest.raises(InvalidKey):
        validate_key(key)


def test_prefix_allows_partial_segments_and_trailing_slash():
    for prefix in ("", "raw", "raw/", "raw/ne", "raw/news/year=2026/"):
        assert validate_prefix(prefix) == prefix
    with pytest.raises(InvalidKey):
        validate_prefix("raw/../")
