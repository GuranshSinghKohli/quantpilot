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
