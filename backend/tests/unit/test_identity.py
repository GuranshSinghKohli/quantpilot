from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.identity import (
    chunk_identity,
    content_hash,
    document_identity,
    event_dedup_key,
    event_evidence_identity,
    event_source_identity,
    normalize_url,
    url_hash,
)


@pytest.mark.parametrize(
    "variant",
    [
        "https://example.com/a/b",
        "HTTPS://Example.COM/a/b/",
        "https://example.com:443/a/b",
        "https://example.com/a/b#frag",
        "https://example.com/a/b?utm_source=x&utm_medium=y",
        "https://example.com/a/b?gclid=abc",
    ],
)
def test_url_variants_share_one_hash(variant):
    assert url_hash(variant) == url_hash("https://example.com/a/b")


def test_meaningful_query_is_kept_and_sorted():
    assert normalize_url("https://x.com/p?b=2&a=1") == "https://x.com/p?a=1&b=2"
    assert url_hash("https://x.com/p?id=1") != url_hash("https://x.com/p?id=2")


def test_relative_url_is_rejected():
    with pytest.raises(ValueError):
        normalize_url("/news/123")


def test_content_hash_ignores_whitespace_only():
    assert content_hash("a  b\n c") == content_hash("a b c")
    assert content_hash("a b c") != content_hash("a b d")


def test_event_key_buckets_by_utc_day():
    entity = uuid.uuid4()
    morning = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
    same_day = morning + timedelta(hours=20)
    next_day = morning + timedelta(days=1)
    key = event_dedup_key("partnership", entity, morning, "h")
    assert key == event_dedup_key("partnership", entity, same_day, "h")
    assert key != event_dedup_key("partnership", entity, next_day, "h")
    assert key != event_dedup_key("acquisition", entity, morning, "h")
    assert key != event_dedup_key("partnership", uuid.uuid4(), morning, "h")


def test_source_backed_event_identity_ignores_title():
    assert event_source_identity("massive.news", "abc", "news") == event_source_identity(
        "massive.news", "abc", "news"
    )
    assert event_source_identity("massive.news", "abc", "news") != event_source_identity(
        "massive.news", "abc", "earnings"
    )
    sid = uuid.uuid4()
    eid = uuid.uuid4()
    assert event_evidence_identity(sid, eid, "news") == event_evidence_identity(sid, eid, "news")
    assert event_evidence_identity(sid, eid, "news") != event_evidence_identity(sid, eid, "other")


def test_document_and_chunk_identity_ignore_title():
    assert document_identity("massive.news", "abc", "h1") == document_identity("massive.news", "abc", "h2")
    assert document_identity(None, None, "h1") != document_identity(None, None, "h2")
    doc = uuid.uuid4()
    assert chunk_identity(doc, "hash", 0) == chunk_identity(doc, "hash", 0)
    assert chunk_identity(doc, "hash", 0) != chunk_identity(doc, "hash", 1)
