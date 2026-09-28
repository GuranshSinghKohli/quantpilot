"""PostgreSQL row locks for the research outbox. Disposable database only."""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.core.settings import get_settings
from app.domain.models import ResearchJob, ResearchOutbox
from app.research.outbox import claim_pending
from app.research.submit import submit_research
from app.retrieval.package import ResearchRequest


def test_skip_locked_publishers_do_not_share_a_row(pg_engine):
    Session = sessionmaker(bind=pg_engine)
    writer = Session()
    job = submit_research(
        writer,
        ResearchRequest(query="AMD partnership with Meta"),
        owner_key="research-api",
        idempotency_key="pg-lock",
        correlation_id="corr-pg",
        settings=get_settings(),
    )
    job_id = job.id
    writer.commit()
    holder = Session()
    other = Session()
    try:
        held = claim_pending(holder, "holder", limit=5, lease_seconds=30)
        assert [row.research_job_id for row in held] == [job_id]
        skipped = claim_pending(other, "other", limit=5, lease_seconds=30)
        assert skipped == []
    finally:
        holder.rollback()
        other.rollback()
        holder.close()
        other.close()
        cleanup = Session()
        cleanup.query(ResearchOutbox).delete()
        cleanup.query(ResearchJob).delete()
        cleanup.commit()
        cleanup.close()
        writer.close()
