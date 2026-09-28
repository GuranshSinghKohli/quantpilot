"""Sources (documents) and the evidence records extracted from them."""

from __future__ import annotations

import uuid
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity import content_hash, normalize_url, url_hash
from app.domain.models import EvidenceRecord, Source
from app.domain.schemas import EvidenceIn, SourceIn
from app.repositories._upsert import insert_ignore


def upsert_source(db: Session, data: SourceIn) -> Tuple[Source, bool]:
    """Keyed on the normalized URL. An existing source is returned unchanged;
    recording new versions of changed content arrives with raw-object ingestion."""
    key = url_hash(data.url)
    created = insert_ignore(
        db,
        Source,
        {
            "id": uuid.uuid4(),
            "source_type": data.source_type.value,
            "publisher": data.publisher,
            "url": normalize_url(data.url),
            "url_hash": key,
            "title": data.title,
            "published_at": data.published_at,
            "retrieved_at": data.retrieved_at,
            "content_hash": content_hash(data.content_text),
            "raw_uri": data.raw_uri,
            "metadata": data.metadata,
        },
        ["url_hash"],
    )
    source = db.scalars(select(Source).where(Source.url_hash == key)).one()
    return source, created


def add_evidence(db: Session, source_id: uuid.UUID, data: EvidenceIn) -> Tuple[EvidenceRecord, bool]:
    """Keyed on (source, excerpt content): the same excerpt of a source is one record."""
    digest = content_hash(data.excerpt)
    created = insert_ignore(
        db,
        EvidenceRecord,
        {
            "id": uuid.uuid4(),
            "source_id": source_id,
            "document_id": data.document_id,
            "evidence_type": data.evidence_type.value,
            "excerpt": data.excerpt,
            "locator": data.locator,
            "content_hash": digest,
            "extraction_method": data.extraction_method,
            "extracted_at": data.extracted_at,
        },
        ["source_id", "content_hash"],
    )
    record = db.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.source_id == source_id, EvidenceRecord.content_hash == digest
        )
    ).one()
    return record, created
