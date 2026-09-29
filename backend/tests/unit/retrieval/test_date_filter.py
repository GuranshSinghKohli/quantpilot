"""Fixed-timestamp date windows. No wall clock."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from app.domain.enums import EventStatus, EventType, SourceType
from app.domain.models import Event
from app.domain.schemas import SourceIn
from app.repositories import sources
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.search import search
from app.retrieval.store import embed_documents
from app.retrieval.structured import load_events
from app.retrieval.time import NaiveTimestamp, as_stored_utc, as_utc, day_window
from tests.unit.retrieval.helpers import close_db, migrated_db

START = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
EASTERN = timezone(timedelta(hours=-5))


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
    finally:
        close_db(session)


def _source(db, url: str, text: str, published_at, retrieved_at):
    sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url=url,
            retrieved_at=retrieved_at,
            content_text=text,
            title=text,
            published_at=published_at,
            metadata={"summary": text},
        ),
    )


def _ready(db):
    index_documents(db)
    embed_documents(db, provider=FakeEmbeddingProvider())


def _titles(db, start, end):
    result = search(
        db,
        "alpha report",
        published_after=start,
        published_before=end,
        top_k=10,
        provider=FakeEmbeddingProvider(),
    )
    return {hit.title for hit in result["hits"]}


def test_half_open_boundaries_and_offsets(db):
    _source(db, "https://example.com/inside", "alpha report inside the open window", START + timedelta(hours=1), START)
    _source(db, "https://example.com/start", "alpha report start boundary included", START, START + timedelta(days=9))
    _source(db, "https://example.com/end", "alpha report end boundary excluded", START + timedelta(days=1), START)
    _source(
        db,
        "https://example.com/before",
        "alpha report before the start bound",
        START - timedelta(microseconds=1),
        START,
    )
    _source(
        db,
        "https://example.com/after",
        "alpha report after the end bound",
        START + timedelta(days=1, microseconds=1),
        START,
    )
    _source(db, "https://example.com/missing", "alpha report missing publication time", None, START + timedelta(days=3))
    _ready(db)
    end = START + timedelta(days=1)
    titles = _titles(db, START, end)
    assert "alpha report inside the open window" in titles
    assert "alpha report start boundary included" in titles
    assert "alpha report end boundary excluded" not in titles
    assert "alpha report before the start bound" not in titles
    assert "alpha report after the end bound" not in titles
    assert "alpha report missing publication time" not in titles
    offset_start = START.astimezone(EASTERN)
    assert _titles(db, offset_start, end.astimezone(EASTERN)) == titles


def test_date_only_range_covers_the_last_civil_day(db):
    first = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    second_evening = datetime(2026, 1, 2, 23, 0, tzinfo=timezone.utc)
    third = datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc)
    _source(db, "https://example.com/d1", "alpha report day one publication", first, third)
    _source(db, "https://example.com/d2", "alpha report day two publication", second_evening, first)
    _source(db, "https://example.com/d3", "alpha report day three publication", third, first)
    _ready(db)
    start, end = day_window(date(2026, 1, 1), date(2026, 1, 2))
    titles = _titles(db, start, end)
    assert titles == {"alpha report day one publication", "alpha report day two publication"}


def test_event_occurrence_is_not_document_publication(db):
    published = START
    occurred = START + timedelta(days=10)
    _source(db, "https://example.com/story", "alpha report story publication date", published, occurred)
    _ready(db)
    event = Event(
        event_type=EventType.PARTNERSHIP.value,
        title="later occurrence",
        occurred_at=occurred,
        published_at=published,
        detected_at=published,
        dedup_key="date-filter-occurrence",
        status=EventStatus.CONFIRMED.value,
    )
    db.add(event)
    db.flush()
    publication_window = load_events(db, entity_ids=[], event_ids=[], date_from=published, date_to=published + timedelta(seconds=1), limit=10)
    occurrence_window = load_events(
        db,
        entity_ids=[],
        event_ids=[],
        date_from=occurred,
        date_to=occurred + timedelta(seconds=1),
        limit=10,
    )
    assert publication_window == []
    assert [row["title"] for row in occurrence_window] == ["later occurrence"]
    assert _titles(db, published, published + timedelta(seconds=1)) == {"alpha report story publication date"}
    assert _titles(db, occurred, occurred + timedelta(seconds=1)) == set()


def test_naive_input_is_rejected_and_stored_naive_is_utc():
    with pytest.raises(NaiveTimestamp):
        as_utc(datetime(2026, 1, 15, 12, 0))
    stored = as_stored_utc(datetime(2026, 1, 15, 12, 0))
    assert stored == START


def test_dst_offset_normalizes_to_the_same_utc_instant():
    eastern = ZoneInfo("America/New_York")
    before = datetime(2026, 3, 8, 1, 30, tzinfo=eastern)
    utc = datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc)
    assert as_utc(before) == utc
    assert as_utc(utc) == utc
