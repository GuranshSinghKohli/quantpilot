"""Controlled vocabularies for the core domain.

Stored as VARCHAR + CHECK constraint (cheaper to evolve than native enums).
Values are part of the data contract with future Kafka topics and Neo4j labels.
"""

from __future__ import annotations

from enum import StrEnum


class EntityType(StrEnum):
    COMPANY = "company"
    TECHNOLOGY = "technology"
    ORGANIZATION = "organization"
    PERSON = "person"


class SourceType(StrEnum):
    NEWS = "news"
    FILING = "filing"
    PRESS_RELEASE = "press_release"
    RESEARCH = "research"
    MARKET_DATA = "market_data"
    WEB = "web"


class EventType(StrEnum):
    PRODUCT_LAUNCH = "product_launch"
    PARTNERSHIP = "partnership"
    INVESTMENT = "investment"
    ACQUISITION = "acquisition"
    FUNDING = "funding"
    EARNINGS = "earnings"
    FILING = "filing"
    REGULATORY = "regulatory"
    EXECUTIVE_CHANGE = "executive_change"
    MARKET_MOVE = "market_move"
    OTHER = "other"


class EventStatus(StrEnum):
    DETECTED = "detected"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class EventEntityRole(StrEnum):
    SUBJECT = "subject"
    COUNTERPARTY = "counterparty"
    MENTIONED = "mentioned"


class RelationshipType(StrEnum):
    """Spec relationship vocabulary (maps 1:1 to future Neo4j edge types)."""

    INVESTED_IN = "INVESTED_IN"
    PARTNERED_WITH = "PARTNERED_WITH"
    SUPPLIES = "SUPPLIES"
    COMPETES_WITH = "COMPETES_WITH"
    USES = "USES"
    ACQUIRED = "ACQUIRED"
    DEVELOPS = "DEVELOPS"
    OWNS = "OWNS"
    CUSTOMERS_OF = "CUSTOMERS_OF"
    DEPENDS_ON = "DEPENDS_ON"


class RelationshipStatus(StrEnum):
    ASSERTED = "asserted"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class WindowLabel(StrEnum):
    """Event-impact windows relative to the event date (trading days)."""

    PRE_1D = "-1D"
    POST_1D = "+1D"
    POST_3D = "+3D"
    POST_5D = "+5D"
    POST_20D = "+20D"


class ResearchJobType(StrEnum):
    EVENT_RESEARCH = "event_research"
    COMPANY_RESEARCH = "company_research"
    TECHNOLOGY_RESEARCH = "technology_research"


class ResearchJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ClaimKind(StrEnum):
    """Epistemic label: stated by a source, measured, reasoned, or conditional."""

    FACT = "FACT"
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    SCENARIO = "SCENARIO"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"


# --- ingestion pipeline (migration 0005) ---------------------------------------


class IngestionDomain(StrEnum):
    NEWS = "news"
    MARKET = "market"
    COMPANY = "company"


class IngestionRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class IngestionRecordStatus(StrEnum):
    """Lifecycle of one ingested event; legal moves are in ``RECORD_TRANSITIONS``."""

    PENDING = "pending"
    PUBLISH_FAILED = "publish_failed"
    PUBLISHED = "published"
    DLQ = "dlq"
    LANDED = "landed"
    PROCESSED = "processed"
    LOADED = "loaded"


_S = IngestionRecordStatus

# target status -> statuses it may be reached from. Updates never move a record
# backwards: the lake-writer can land an event before the producer's delivery
# callback marks it published, and that late callback must not regress it.
RECORD_TRANSITIONS = {
    _S.PUBLISHED: frozenset({_S.PENDING, _S.PUBLISH_FAILED}),
    _S.PUBLISH_FAILED: frozenset({_S.PENDING}),
    _S.DLQ: frozenset({_S.PENDING, _S.PUBLISH_FAILED, _S.PUBLISHED}),
    _S.LANDED: frozenset({_S.PENDING, _S.PUBLISH_FAILED, _S.PUBLISHED, _S.DLQ}),
    _S.PROCESSED: frozenset({_S.LANDED}),
    _S.LOADED: frozenset({_S.LANDED, _S.PROCESSED}),
}

# Already safely in the system: re-ingesting the same event is a duplicate.
# DLQ is not here: most lake-writer dead letters are exhausted transient retries,
# so re-ingesting the event is the recovery path.
RECORD_DELIVERED = frozenset({_S.PUBLISHED, _S.LANDED, _S.PROCESSED, _S.LOADED})
# May be reset to pending by a new ingestion attempt of the same event.
RECORD_RETRYABLE = frozenset({_S.PENDING, _S.PUBLISH_FAILED, _S.DLQ})


class ProcessingJobStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProcessingMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
