"""Event candidates are not canonical events until ACCEPTED."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pipeline_context import pipeline_bind
from app.domain.enums import CandidateStatus, EntityType, EventEntityRole, EventStatus, EventType, ResolutionStatus
from app.domain.identity import event_evidence_identity, event_source_identity
from app.domain.ingestion_models import NewsArticle
from app.domain.models import Event, EventCandidate, EventEntity, EvidenceRecord, Source
from app.graph.errors import AmbiguousEntity, ProvenanceRequired, UnresolvedEntity
from app.graph.resolve import EntityRef, Resolution, resolve_entity
from app.market.metrics import MARKET_METRICS
from app.observability.logger import get_logger, log_event
from app.repositories._upsert import insert_ignore

logger = get_logger("market")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventAssociationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: EntityRef
    role: EventEntityRole


class EventCandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    title: str = Field(min_length=1, max_length=512)
    source_id: uuid.UUID
    evidence_id: Optional[uuid.UUID] = None
    summary: Optional[str] = None
    occurred_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    identity_key: Optional[str] = None
    associations: List[EventAssociationIn] = Field(default_factory=list)
    tickers: List[str] = Field(default_factory=list)


def _identity_key(db: Session, proposal: EventCandidateProposal, source: Source) -> str:
    if proposal.identity_key:
        return proposal.identity_key
    if proposal.evidence_id is not None:
        return event_evidence_identity(proposal.source_id, proposal.evidence_id, proposal.event_type.value)
    article = db.get(NewsArticle, proposal.source_id)
    if article is not None and article.external_id:
        return event_source_identity(article.source_key, article.external_id, proposal.event_type.value)
    return event_source_identity(source.url_hash, str(proposal.source_id), proposal.event_type.value)


def _require_source(db: Session, proposal: EventCandidateProposal) -> Source:
    source = db.get(Source, proposal.source_id)
    if source is None:
        raise ProvenanceRequired("source_id does not exist")
    if proposal.evidence_id is not None:
        evidence = db.get(EvidenceRecord, proposal.evidence_id)
        if evidence is None:
            raise ProvenanceRequired("evidence_id does not exist")
        if evidence.source_id != proposal.source_id:
            raise ProvenanceRequired("evidence_id does not belong to source_id")
    return source


def _resolve_ticker(db: Session, ticker: str) -> Resolution:
    return resolve_entity(db, EntityRef(ticker=ticker, entity_type=EntityType.COMPANY))


class EventCandidateService:
    """Propose, accept, and reject event candidates. Acceptance is idempotent."""

    def propose(self, db: Session, proposal: EventCandidateProposal) -> Tuple[EventCandidate, bool]:
        source = _require_source(db, proposal)
        key = _identity_key(db, proposal, source)
        existing = db.scalars(select(EventCandidate).where(EventCandidate.identity_key == key)).first()
        if existing is not None:
            MARKET_METRICS.inc(
                "event_candidates_total",
                event_type=proposal.event_type.value,
                status="duplicate",
                operation="propose",
            )
            return existing, False
        row = EventCandidate(
            id=uuid.uuid4(),
            identity_key=key,
            event_type=proposal.event_type.value,
            title=proposal.title,
            summary=proposal.summary,
            occurred_at=proposal.occurred_at,
            published_at=proposal.published_at or source.published_at,
            source_id=proposal.source_id,
            evidence_id=proposal.evidence_id,
            status=CandidateStatus.PENDING.value,
            confidence=proposal.confidence,
        )
        db.add(row)
        db.flush()
        MARKET_METRICS.inc(
            "event_candidates_total",
            event_type=row.event_type,
            status=row.status,
            operation="propose",
        )
        log_event(
            logger,
            20,
            "EVENT_CANDIDATE_CREATED",
            component="market",
            operation="propose",
            event_type=row.event_type,
            status=row.status,
        )
        return row, True

    def propose_from_news(self, db: Session, article: NewsArticle) -> Optional[Tuple[EventCandidate, bool]]:
        """Only structured ticker tags become NEWS candidates. No tickers → stay a document."""
        tickers = [str(t).upper().strip() for t in (article.tickers or []) if str(t).strip()]
        if not tickers:
            return None
        source = db.get(Source, article.source_id)
        if source is None:
            raise ProvenanceRequired("news article source_id does not exist")
        proposal = EventCandidateProposal(
            event_type=EventType.NEWS,
            title=(source.title or article.summary or article.external_id)[:512],
            summary=article.summary,
            occurred_at=None,
            published_at=source.published_at,
            source_id=article.source_id,
            identity_key=event_source_identity(article.source_key, article.external_id, EventType.NEWS.value),
            tickers=tickers,
            confidence=0.6,
        )
        return self.propose(db, proposal)

    def accept(
        self,
        db: Session,
        candidate_id: uuid.UUID,
        *,
        associations: Optional[Sequence[EventAssociationIn]] = None,
        tickers: Optional[Sequence[str]] = None,
    ) -> Tuple[EventCandidate, Event]:
        row = db.get(EventCandidate, candidate_id)
        if row is None:
            raise ProvenanceRequired("candidate does not exist")
        if row.status == CandidateStatus.REJECTED.value:
            raise ProvenanceRequired("a rejected candidate cannot be accepted")
        if row.status == CandidateStatus.AMBIGUOUS.value:
            raise AmbiguousEntity("ambiguous candidate will not be accepted")
        if row.status == CandidateStatus.ACCEPTED.value and row.accepted_event_id:
            event = db.get(Event, row.accepted_event_id)
            if event is None:
                raise ProvenanceRequired("accepted event is missing")
            return row, event

        event = self._upsert_confirmed_event(db, row)
        links = self._resolved_links(db, row, associations=associations, tickers=tickers)
        for entity_id, role, confidence in links:
            created = insert_ignore(
                db,
                EventEntity,
                {
                    "event_id": event.id,
                    "entity_id": entity_id,
                    "role": role.value,
                    "source_id": row.source_id,
                    "evidence_id": row.evidence_id,
                    "confidence": confidence,
                },
                ["event_id", "entity_id", "role"],
            )
            if created:
                MARKET_METRICS.inc(
                    "event_entity_links_total",
                    event_type=row.event_type,
                    status="accepted",
                    operation="associate",
                )
        row.status = CandidateStatus.ACCEPTED.value
        row.accepted_event_id = event.id
        db.flush()
        MARKET_METRICS.inc(
            "event_candidates_total",
            event_type=row.event_type,
            status=CandidateStatus.ACCEPTED.value,
            operation="accept",
        )
        log_event(
            logger,
            20,
            "EVENT_CANDIDATE_ACCEPTED",
            component="market",
            operation="accept",
            event_type=row.event_type,
            status=row.status,
        )
        return row, event

    def reject(self, db: Session, candidate_id: uuid.UUID) -> EventCandidate:
        row = db.get(EventCandidate, candidate_id)
        if row is None:
            raise ProvenanceRequired("candidate does not exist")
        if row.status == CandidateStatus.ACCEPTED.value:
            raise ProvenanceRequired("an accepted candidate cannot be rejected")
        row.status = CandidateStatus.REJECTED.value
        db.flush()
        MARKET_METRICS.inc(
            "event_candidates_total",
            event_type=row.event_type,
            status=CandidateStatus.REJECTED.value,
            operation="reject",
        )
        log_event(
            logger,
            20,
            "EVENT_CANDIDATE_REJECTED",
            component="market",
            operation="reject",
            event_type=row.event_type,
            status=row.status,
        )
        return row

    def _upsert_confirmed_event(self, db: Session, row: EventCandidate) -> Event:
        created = insert_ignore(
            db,
            Event,
            {
                "id": uuid.uuid4(),
                "event_type": row.event_type,
                "title": row.title,
                "summary": row.summary,
                "occurred_at": row.occurred_at,
                "published_at": row.published_at,
                "detected_at": _now(),
                "primary_source_id": row.source_id,
                "evidence_id": row.evidence_id,
                "dedup_key": row.identity_key,
                "status": EventStatus.CONFIRMED.value,
            },
            ["dedup_key"],
        )
        event = db.scalars(select(Event).where(Event.dedup_key == row.identity_key)).one()
        if event.status != EventStatus.REJECTED.value:
            event.status = EventStatus.CONFIRMED.value
        if created:
            MARKET_METRICS.inc(
                "events_created_total",
                event_type=event.event_type,
                status=event.status,
                operation="accept",
            )
        return event

    def _resolved_links(
        self,
        db: Session,
        row: EventCandidate,
        *,
        associations: Optional[Sequence[EventAssociationIn]],
        tickers: Optional[Sequence[str]],
    ) -> List[Tuple[uuid.UUID, EventEntityRole, Optional[float]]]:
        if associations:
            links = []
            for item in associations:
                result = resolve_entity(db, item.entity)
                if result.status.value == "UNRESOLVED":
                    raise UnresolvedEntity("association entity could not be resolved")
                if result.status.value == "AMBIGUOUS":
                    raise AmbiguousEntity("association entity is ambiguous; will not guess")
                links.append((result.require(), item.role, row.confidence))
            return links

        symbols = [str(t).upper().strip() for t in (tickers or []) if str(t).strip()]
        if not symbols:
            article = db.get(NewsArticle, row.source_id)
            symbols = [str(t).upper().strip() for t in ((article.tickers if article else []) or []) if str(t).strip()]

        links: List[Tuple[uuid.UUID, EventEntityRole, Optional[float]]] = []
        resolved_first = True
        for ticker in symbols:
            result = _resolve_ticker(db, ticker)
            if result.status.value in {"UNRESOLVED", "AMBIGUOUS"}:
                log_event(
                    logger,
                    20,
                    "EVENT_ENTITY_SKIPPED",
                    component="market",
                    operation="associate",
                    event_type=row.event_type,
                    status=result.status.value,
                )
                continue
            role = EventEntityRole.SUBJECT if resolved_first else EventEntityRole.OTHER
            resolved_first = False
            links.append((result.require(), role, row.confidence))
        return links


def propose_news_candidates(db: Session) -> dict:
    """Create PENDING NEWS candidates from canonical articles that have ticker tags."""
    started = datetime.now(timezone.utc)
    service = EventCandidateService()
    created = skipped = failed = 0
    with pipeline_bind(component="market", operation="events-from-news"):
        articles = list(db.scalars(select(NewsArticle)))
        for article in articles:
            try:
                result = service.propose_from_news(db, article)
                if result is None:
                    skipped += 1
                    continue
                _row, was_created = result
                created += int(was_created)
                skipped += int(not was_created)
            except Exception:
                failed += 1
                log_event(
                    logger,
                    40,
                    "EVENT_CANDIDATE_NEWS_FAILED",
                    component="market",
                    operation="events-from-news",
                    status="failed",
                    event_type=EventType.NEWS.value,
                )
        db.flush()
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    log_event(
        logger,
        20,
        "EVENT_CANDIDATES_FROM_NEWS",
        component="market",
        operation="events-from-news",
        status="ok" if failed == 0 else "partial",
        event_type=EventType.NEWS.value,
        duration_ms=int(duration * 1000),
    )
    return {
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "duration_seconds": round(duration, 3),
    }


def accept_resolved_news_candidates(db: Session) -> dict:
    """Confirm pending NEWS candidates whose provider tickers resolve to an entity.

    Event type stays NEWS. A candidate with no resolved ticker stays PENDING.
    This does not infer a partnership, acquisition, product launch, or technology use.
    """
    started = datetime.now(timezone.utc)
    service = EventCandidateService()
    accepted = left_pending = failed = 0
    rows = list(
        db.scalars(
            select(EventCandidate).where(
                EventCandidate.status == CandidateStatus.PENDING.value,
                EventCandidate.event_type == EventType.NEWS.value,
            )
        )
    )
    with pipeline_bind(component="market", operation="accept-resolved-news"):
        for row in rows:
            article = db.get(NewsArticle, row.source_id)
            tickers = [str(t).upper().strip() for t in ((article.tickers if article else None) or []) if str(t).strip()]
            if not any(_resolve_ticker(db, ticker).status == ResolutionStatus.RESOLVED for ticker in tickers):
                left_pending += 1
                continue
            try:
                service.accept(db, row.id)
                accepted += 1
            except Exception:
                failed += 1
                log_event(
                    logger,
                    40,
                    "EVENT_CANDIDATE_ACCEPT_FAILED",
                    component="market",
                    operation="accept-resolved-news",
                    status="failed",
                    event_type=EventType.NEWS.value,
                )
        db.flush()
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    return {
        "examined": len(rows),
        "accepted": accepted,
        "left_pending": left_pending,
        "failed": failed,
        "duration_seconds": round(duration, 3),
    }
