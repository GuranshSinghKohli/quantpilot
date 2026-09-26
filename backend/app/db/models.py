from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    portfolios: Mapped[List["Portfolio"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Default")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    user: Mapped["User"] = relationship(back_populates="portfolios")
    holdings: Mapped[List["Holding"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="Holding.added_at.desc()",
    )


class Holding(Base):
    """Watchlist / portfolio position (Phase 7: same entity)."""

    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("portfolio_id", "ticker", name="uq_portfolio_ticker"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    shares: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")

    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")


class AlertEvent(Base):
    """In-app notification for a material investigation."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    alert_type: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(280), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    observed_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # PRD v3 Phase 12 — link material investigation notifications to ledger cases
    investigation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("investigations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    materiality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Phase 10 — optional operator feedback on an alert.
    feedback: Mapped[str] = mapped_column(String(32), default="")

    user: Mapped["User"] = relationship()


class AnalysisRun(Base):
    """Per-owner analysis history entry (authenticated user or guest device)."""

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # owner_key is "user:{id}" for signed-in accounts or "guest:{uuid}" for a
    # browser device. History is never shared across different owner keys.
    owner_key: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    headline: Mapped[str] = mapped_column(String(512), default="")
    risk_level: Mapped[str] = mapped_column(String(32), default="")
    chroma_doc_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )

    user: Mapped[Optional["User"]] = relationship()


# ---------------------------------------------------------------------------
# Phase 7 (PRD v3) — Evidence Ledger
# ---------------------------------------------------------------------------


class Investigation(Base):
    """One row per investigation: why a ticker moved (or on-demand ask)."""

    __tablename__ = "investigations"
    __table_args__ = (
        Index("ix_investigations_owner_ticker_created", "owner_key", "ticker", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_key: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    trigger_reason: Mapped[str] = mapped_column(String(64), default="on_demand")
    # planning | collecting | verifying | complete | failed | skipped_market_noise
    status: Mapped[str] = mapped_column(String(32), default="planning", index=True)
    move_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    window_label: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    # Phase 9 — verification + Devil's Advocate audit trail
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    da_outcome_json: Mapped[str] = mapped_column(Text, default="{}")
    # PRD v3 Phase 13 — earnings / macro / investigation brief
    roster_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[Optional["User"]] = relationship()
    claims: Mapped[List["Claim"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="Claim.rank.asc()",
    )
    evidence_items: Mapped[List["EvidenceItem"]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="EvidenceItem.created_at.asc()",
    )


class Claim(Base):
    """Candidate explanatory hypothesis for an investigation."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    statement: Mapped[str] = mapped_column(Text, default="")
    # supports_move | contradicts | market_noise | unknown
    stance: Mapped[str] = mapped_column(String(32), default="unknown")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    devil_advocate_notes: Mapped[str] = mapped_column(Text, default="")
    # Phase 5 — FACT | INTERPRETATION | CAUSAL_EXPLANATION | RISK | CONCLUSION
    claim_type: Mapped[str] = mapped_column(String(32), default="CAUSAL_EXPLANATION")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    investigation: Mapped["Investigation"] = relationship(back_populates="claims")
    evidence_links: Mapped[List["ClaimEvidenceLink"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
    )


class EvidenceItem(Base):
    """Retrieved evidence unit (news, filing excerpt, IR page, price datapoint)."""

    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    # news | filing | transcript | ir_page | price | options | macro | other
    source_type: Mapped[str] = mapped_column(String(32), default="other", index=True)
    # mcp | openclaw | httpx | user | system
    retrieval_method: Mapped[str] = mapped_column(String(32), default="system")
    title: Mapped[str] = mapped_column(String(280), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    raw_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    # Phase 5 — SHA-256 of normalized excerpt/title for exact-content dedup
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    investigation: Mapped["Investigation"] = relationship(back_populates="evidence_items")
    claim_links: Mapped[List["ClaimEvidenceLink"]] = relationship(
        back_populates="evidence",
        cascade="all, delete-orphan",
    )


class ClaimEvidenceLink(Base):
    """Many-to-many: which evidence supports or contradicts which claim."""

    __tablename__ = "claim_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "evidence_id",
            name="uq_claim_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        index=True,
    )
    evidence_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        index=True,
    )
    # supports | contradicts | context
    relation: Mapped[str] = mapped_column(String(32), default="supports")
    note: Mapped[str] = mapped_column(String(280), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    claim: Mapped["Claim"] = relationship(back_populates="evidence_links")
    evidence: Mapped["EvidenceItem"] = relationship(back_populates="claim_links")


class CompanyMemoryItem(Base):
    """Phase 8 — append-only, owner-scoped, source-backed company memory."""

    __tablename__ = "company_memory_items"
    __table_args__ = (
        Index("ix_company_memory_owner_ticker", "owner_key", "ticker"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_key: Mapped[str] = mapped_column(String(80), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    # COMPANY_FACT | MANAGEMENT_STATEMENT | RISK | EVENT | INVESTIGATION_CONCLUSION | THEME
    memory_type: Mapped[str] = mapped_column(String(32), index=True)
    statement: Mapped[str] = mapped_column(Text, default="")
    theme_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    # ACTIVE | CONFIRMED | WEAKENED | CONTRADICTED | OUTDATED | ARCHIVED
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    polarity: Mapped[str] = mapped_column(String(16), default="NEUTRAL")
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    evidence_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    investigation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("investigations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    source_type: Mapped[str] = mapped_column(String(32), default="")
    supersedes_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("company_memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    extra_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )
