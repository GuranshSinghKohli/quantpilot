"""Document identity, normalization, and deterministic chunking."""

from __future__ import annotations

import pytest

from app.domain.identity import chunk_identity, content_hash, document_identity, document_uuid
from app.domain.models import RetrievalChunk, RetrievalDocument
from app.retrieval.chunking import chunk_document
from app.retrieval.documents import index_documents, upsert_document_from_source
from app.retrieval.normalize import normalize_document_text
from tests.unit.retrieval.helpers import close_db, company, migrated_db, news_source


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_identity_prefers_vendor_id_not_title():
    a = document_identity("massive.news", "abc", "urlhash")
    b = document_identity("massive.news", "abc", "other-url")
    c = document_identity("massive.news", "xyz", "urlhash")
    assert a == b
    assert a != c
    assert document_uuid(a) == document_uuid(a)


def test_normalization_is_deterministic_and_preserves_words():
    raw = "<p>NVIDIA   announced\r\n\r\n\r\na partnership.</p>"
    once = normalize_document_text(raw)
    twice = normalize_document_text(raw)
    assert once == twice
    assert "NVIDIA announced" in once.replace("\n", " ")
    assert "<p>" not in once


def test_chunk_ids_and_offsets_are_deterministic():
    text = "Alpha paragraph about NVIDIA.\n\n" + ("Beta sentence. " * 80)
    digest = content_hash(text)
    doc_id = document_uuid("doc")
    first = chunk_document(doc_id, digest, text)
    second = chunk_document(doc_id, digest, text)
    assert first
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].chunk_id == chunk_identity(doc_id, digest, 0)
    assert first[0].content == text[first[0].start_offset : first[0].end_offset]
    assert first[0].end_offset > first[0].start_offset
    empty = chunk_document(doc_id, digest, "   ")
    assert empty == []


def test_duplicate_source_does_not_duplicate_document(db):
    src = news_source(db, "https://example.com/n1", "NVIDIA partners", "NVIDIA and Meta announced a partnership.", ["NVDA"])
    first = upsert_document_from_source(db, src)
    second = upsert_document_from_source(db, src)
    assert first["status"] == "inserted"
    assert second["status"] == "skipped"
    assert db.query(RetrievalDocument).count() == 1


def test_content_change_versions_without_destroying_old_chunks(db):
    from sqlalchemy.orm.attributes import flag_modified

    src = news_source(
        db,
        "https://example.com/n2",
        "Original AMD title with enough characters",
        "Original wording about AMD accelerators in a longer paragraph.",
    )
    first = upsert_document_from_source(db, src)
    assert first["status"] == "inserted"
    src.title = "Title revised about AMD accelerators"
    src.metadata_ = {"summary": "Updated wording about AMD accelerators and more tokens."}
    flag_modified(src, "metadata_")
    db.flush()
    report = upsert_document_from_source(db, src)
    assert report["status"] == "updated"
    document = db.query(RetrievalDocument).one()
    assert document.content_version == 2
    assert document.previous_content_hash
    hashes = {row.document_content_hash for row in db.query(RetrievalChunk)}
    assert len(hashes) >= 2


def test_unresolved_ticker_is_not_linked(db):
    company(db, "NVIDIA Corporation", "NVDA", "1045810")
    src = news_source(
        db,
        "https://example.com/n3",
        "Mentions",
        "NVIDIA and ZZQQ were mentioned.",
        ["NVDA", "ZZQQ"],
    )
    upsert_document_from_source(db, src)
    from app.domain.models import DocumentEntity

    links = list(db.query(DocumentEntity))
    assert len(links) == 1
