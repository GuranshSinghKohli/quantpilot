"""Validated inputs for core-domain repositories.

Every write into the shared knowledge base goes through one of these, so
vocabulary, ranges and required provenance are checked before the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import (
    EventEntityRole,
    EventType,
    EvidenceStance,
    EvidenceType,
    RelationshipType,
    ResearchJobType,
    SourceType,
)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceIn(_In):
    source_type: SourceType
    url: str = Field(min_length=1, max_length=2048)
    retrieved_at: datetime
    content_text: str = Field(min_length=1, description="Full text used for content_hash")
    publisher: Optional[str] = Field(default=None, max_length=255)
    title: Optional[str] = Field(default=None, max_length=512)
    published_at: Optional[datetime] = None
    raw_uri: Optional[str] = Field(default=None, max_length=1024)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceIn(_In):
    excerpt: str = Field(min_length=1)
    extraction_method: str = Field(min_length=1, max_length=64)
    extracted_at: datetime
    locator: Dict[str, Any] = Field(default_factory=dict)
    evidence_type: EvidenceType = EvidenceType.DOCUMENT_EXCERPT
    document_id: Optional[uuid.UUID] = None


class CompanyIn(_In):
    legal_name: str = Field(min_length=1, max_length=255)
    ticker: Optional[str] = Field(default=None, max_length=16)
    exchange: Optional[str] = Field(default=None, max_length=32)
    cik: Optional[str] = Field(default=None, max_length=10)
    website: Optional[str] = Field(default=None, max_length=512)

    @field_validator("ticker", "exchange")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else None

    @field_validator("cik")
    @classmethod
    def _cik(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        if not value.isdigit():
            raise ValueError("CIK must be numeric")
        return value.zfill(10)


class TechnologyIn(_In):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = None


class EventEntityIn(_In):
    entity_id: uuid.UUID
    role: EventEntityRole
    source_id: Optional[uuid.UUID] = None
    evidence_id: Optional[uuid.UUID] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class EventIn(_In):
    event_type: EventType
    title: str = Field(min_length=1, max_length=512)
    detected_at: datetime
    summary: Optional[str] = None
    occurred_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    primary_source_id: Optional[uuid.UUID] = None
    evidence_id: Optional[uuid.UUID] = None
    entities: List[EventEntityIn] = Field(default_factory=list)

    @property
    def primary_entity_id(self) -> Optional[uuid.UUID]:
        for link in self.entities:
            if link.role == EventEntityRole.SUBJECT:
                return link.entity_id
        return None


class RelationshipIn(_In):
    subject_entity_id: uuid.UUID
    relationship_type: RelationshipType
    object_entity_id: uuid.UUID
    observed_at: datetime
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

    @model_validator(mode="after")
    def _distinct_and_ordered(self) -> "RelationshipIn":
        if self.subject_entity_id == self.object_entity_id:
            raise ValueError("A relationship needs two different entities")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        return self


class RelationshipEvidenceIn(_In):
    evidence_id: uuid.UUID
    stance: EvidenceStance


class ResearchJobIn(_In):
    owner_key: str = Field(min_length=1, max_length=80)
    job_type: ResearchJobType
    idempotency_key: str = Field(min_length=1, max_length=128)
    request: Dict[str, Any] = Field(default_factory=dict)
    event_id: Optional[uuid.UUID] = None

