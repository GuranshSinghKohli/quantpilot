"""Core domain tables (migration 0003).

Shared knowledge — entities, companies, technologies, sources, evidence records,
events, relationships, market event windows — is global. Research work —
research jobs, agent runs, claims, reports — is scoped by owner_key.

Lineage: report -> research_claim -> claim_evidence -> evidence_record -> source (-> raw_uri).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Dict, List, Optional, Type

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base
from app.domain.enums import (
    AgentRunStatus,
    AliasType,
    ClaimKind,
    EntityType,
    EventEntityRole,
    EventStatus,
    EventType,
    EvidenceStance,
    ProvenanceKind,
    RelationshipStatus,
    RelationshipType,
    ReportStatus,
    ResearchJobStatus,
    ResearchJobType,
    SourceType,
    VerificationStatus,
    WindowLabel,
)

JsonDoc = JSON().with_variant(JSONB(), "postgresql")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _one_of(column: str, values: Type[StrEnum], name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{v.value}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now()
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


# ---------------------------------------------------------------------------
# Shared knowledge
# ---------------------------------------------------------------------------


class Entity(Base):
    """Supertable for anything a relationship can point at (1:1 with graph nodes)."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("entity_type", "canonical_name", name="uq_entities_type_name"),
        Index(
            "uq_entities_source_identity",
            "source_system",
            "source_key",
            unique=True,
            sqlite_where=text("source_system IS NOT NULL AND source_key IS NOT NULL"),
            postgresql_where=text("source_system IS NOT NULL AND source_key IS NOT NULL"),
        ),
        Index("ix_entities_normalized_name", "normalized_name"),
        _one_of("entity_type", EntityType, "ck_entities_entity_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[Optional[str]] = mapped_column(String(64))
    source_key: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    company: Mapped[Optional["Company"]] = relationship(back_populates="entity", uselist=False)
    technology: Mapped[Optional["Technology"]] = relationship(
        back_populates="entity", uselist=False
    )
    aliases: Mapped[List["EntityAlias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("ticker", "exchange", name="uq_companies_ticker_exchange"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(32))
    cik: Mapped[Optional[str]] = mapped_column(String(10), unique=True)
    website: Mapped[Optional[str]] = mapped_column(String(512))
    # Set by the canonical loader (0007); NULL for companies created elsewhere.
    # ``ticker`` is the first listed ticker; ``tickers`` keeps every listed one.
    tickers: Mapped[Optional[List[str]]] = mapped_column(JsonDoc)
    source: Mapped[Optional[str]] = mapped_column(String(64))
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))
    raw_uri: Mapped[Optional[str]] = mapped_column(String(1024))
    # Lineage when the row was loaded by the ingestion pipeline (NULL otherwise).
    source_ingested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("ingestion_records.event_id", ondelete="RESTRICT", name="fk_companies_event_id"),
    )
    processing_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="RESTRICT", name="fk_companies_processing_job_id")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    entity: Mapped[Entity] = relationship(back_populates="company")


class Technology(Base):
    __tablename__ = "technologies"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    entity: Mapped[Entity] = relationship(back_populates="technology")


class EntityAlias(Base):
    """Non-identity names and tickers. Never used as the primary key."""

    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "alias_type",
            "normalized_alias",
            name="uq_entity_aliases_entity_type_norm",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_entity_aliases_confidence",
        ),
        _one_of("alias_type", AliasType, "ck_entity_aliases_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    entity: Mapped[Entity] = relationship(back_populates="aliases")


class Source(Base):
    """One retrievable document, deduplicated by normalized URL hash."""

    __tablename__ = "sources"
    __table_args__ = (_one_of("source_type", SourceType, "ck_sources_source_type"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[Optional[str]] = mapped_column(String(512))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Pointer to the immutable raw object in the lake (set once ingestion lands raw files).
    raw_uri: Mapped[Optional[str]] = mapped_column(String(1024))
    metadata_: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JsonDoc, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = _created_at()


class EvidenceRecord(Base):
    """A located excerpt of a source that can support or contradict claims."""

    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_evidence_records_source_content"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    # Where in the source: {"section": ..., "page": ..., "char_start": ..., "char_end": ...}
    locator: Mapped[Dict[str, Any]] = mapped_column(JsonDoc, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    source: Mapped[Source] = relationship()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        _one_of("event_type", EventType, "ck_events_event_type"),
        _one_of("status", EventStatus, "ck_events_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    primary_source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    # Idempotency: normalized type + primary entity + time bucket + primary source.
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EventStatus.DETECTED.value
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    entities: Mapped[List["EventEntity"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventEntity(Base):
    __tablename__ = "event_entities"
    __table_args__ = (_one_of("role", EventEntityRole, "ck_event_entities_role"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True)

    event: Mapped[Event] = relationship(back_populates="entities")
    entity: Mapped[Entity] = relationship()


class Relationship(Base):
    """Canonical relationship edge. A graph store, when added, is a projection of this table."""

    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint(
            "subject_entity_id",
            "relationship_type",
            "object_entity_id",
            name="uq_relationships_triple",
        ),
        CheckConstraint("subject_entity_id <> object_entity_id", name="ck_relationships_not_self"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_relationships_confidence",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="ck_relationships_valid_range",
        ),
        _one_of("relationship_type", RelationshipType, "ck_relationships_type"),
        _one_of("status", RelationshipStatus, "ck_relationships_status"),
        _one_of("provenance_kind", ProvenanceKind, "ck_relationships_provenance"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RelationshipStatus.ASSERTED.value
    )
    provenance_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ProvenanceKind.OBSERVED.value
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    evidence_links: Mapped[List["RelationshipEvidence"]] = relationship(
        back_populates="edge", cascade="all, delete-orphan"
    )


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"
    __table_args__ = (_one_of("stance", EvidenceStance, "ck_relationship_evidence_stance"),)

    relationship_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("relationships.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_records.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    stance: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    edge: Mapped[Relationship] = relationship(back_populates="evidence_links")
    evidence: Mapped[EvidenceRecord] = relationship()


class GraphProjection(Base):
    """Last projection attempt for one Postgres entity or relationship. Not a second SoT."""

    __tablename__ = "graph_projections"
    __table_args__ = (
        UniqueConstraint("record_kind", "record_id", name="uq_graph_projections_record"),
        CheckConstraint("record_kind IN ('entity', 'relationship')", name="ck_graph_projections_kind"),
        CheckConstraint("status IN ('success', 'failed')", name="ck_graph_projections_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    projected_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class MarketEventWindow(Base):
    """Event Impact Analysis for one ticker and one window. Describes, never attributes cause."""

    __tablename__ = "market_event_windows"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "ticker",
            "window_label",
            "method_version",
            name="uq_market_event_windows_event_ticker_window_method",
        ),
        CheckConstraint("window_start <= window_end", name="ck_market_event_windows_range"),
        _one_of("window_label", WindowLabel, "ck_market_event_windows_label"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    benchmark_ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    window_label: Mapped[str] = mapped_column(String(8), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    return_pct: Mapped[Optional[float]] = mapped_column(Float)
    benchmark_return_pct: Mapped[Optional[float]] = mapped_column(Float)
    abnormal_return_pct: Mapped[Optional[float]] = mapped_column(Float)
    volume_change_pct: Mapped[Optional[float]] = mapped_column(Float)
    volatility: Mapped[Optional[float]] = mapped_column(Float)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False)
    method_version: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# Research work (owner-scoped)
# ---------------------------------------------------------------------------


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    __table_args__ = (
        Index("ix_research_jobs_owner_created", "owner_key", "queued_at"),
        UniqueConstraint("owner_key", "idempotency_key", name="uq_research_jobs_owner_idempotency"),
        _one_of("job_type", ResearchJobType, "ck_research_jobs_job_type"),
        _one_of("status", ResearchJobStatus, "ck_research_jobs_status"),
        CheckConstraint("attempts >= 0", name="ck_research_jobs_attempts"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_key: Mapped[str] = mapped_column(String(80), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ResearchJobStatus.QUEUED.value, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request: Mapped[Dict[str, Any]] = mapped_column(JsonDoc, nullable=False, default=dict)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True
    )
    current_stage: Mapped[Optional[str]] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = _updated_at()

    agent_runs: Mapped[List["AgentRun"]] = relationship(
        back_populates="research_job", cascade="all, delete-orphan"
    )
    claims: Mapped[List["ResearchClaim"]] = relationship(
        back_populates="research_job", cascade="all, delete-orphan"
    )
    reports: Mapped[List["Report"]] = relationship(
        back_populates="research_job", cascade="all, delete-orphan"
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "research_job_id", "agent_name", "attempt", name="uq_agent_runs_job_agent_attempt"
        ),
        _one_of("status", AgentRunStatus, "ck_agent_runs_status"),
        CheckConstraint("attempt >= 1", name="ck_agent_runs_attempt"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AgentRunStatus.RUNNING.value
    )
    model: Mapped[Optional[str]] = mapped_column(String(64))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32))
    # Hash of the agent's inputs, so a retry can reuse an identical completed run.
    input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    research_job: Mapped[ResearchJob] = relationship(back_populates="agent_runs")


class ResearchClaim(Base):
    __tablename__ = "research_claims"
    __table_args__ = (
        _one_of("claim_kind", ClaimKind, "ck_research_claims_kind"),
        _one_of("verification_status", VerificationStatus, "ck_research_claims_verification"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_research_claims_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VerificationStatus.UNVERIFIED.value
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    uncertainty_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    research_job: Mapped[ResearchJob] = relationship(back_populates="claims")
    evidence_links: Mapped[List["ClaimEvidence"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (_one_of("stance", EvidenceStance, "ck_claim_evidence_stance"),)

    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_records.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    stance: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    assessed_by_agent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()

    claim: Mapped[ResearchClaim] = relationship(back_populates="evidence_links")
    evidence: Mapped[EvidenceRecord] = relationship()


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("research_job_id", "version", name="uq_reports_job_version"),
        _one_of("status", ReportStatus, "ck_reports_status"),
        CheckConstraint("version >= 1", name="ck_reports_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReportStatus.DRAFT.value
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    sections: Mapped[List[Dict[str, Any]]] = mapped_column(JsonDoc, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()

    research_job: Mapped[ResearchJob] = relationship(back_populates="reports")


# Registered last: the ingestion tables reference helpers above and are
# referenced by Company, so importing this module always yields full metadata.
from app.domain import ingestion_models  # noqa: E402,F401
