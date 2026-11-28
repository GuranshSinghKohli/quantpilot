"""Persisted claims from an explicit accelerator excerpt. Disposable PostgreSQL."""

from app.domain.models import ResearchClaim
from app.research.llm import FakeLLMProvider
from app.research.service import run_research
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from tests.unit.research.helpers import seed_partnership


def test_supported_accelerator_quote_is_persisted(db):
    world = seed_partnership(db)
    result = run_research(
        db,
        ResearchRequest(query="Meta data center accelerators", entity_ids=[world["meta"].entity_id]),
        llm=FakeLLMProvider(),
        embed_provider=FakeEmbeddingProvider(),
        persist=True,
    )
    db.flush()
    assert result.research_job_id is not None
    rows = db.query(ResearchClaim).filter_by(research_job_id=result.research_job_id).all()
    assert rows
    text = " ".join(row.statement.lower() for row in rows)
    assert "accelerator" in text and "deploy" in text
    assert any(row.verification_status == "supported" and row.claim_kind == "FACT" for row in rows)
    assert "documented uses" not in text
