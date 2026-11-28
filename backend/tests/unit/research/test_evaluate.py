"""Development evaluation fixture exists and is labeled as such."""

from __future__ import annotations

import pytest
from app.research.evaluate import evaluate_research
from tests.unit.research.helpers import close_db, db_session


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_evaluate_research_is_labeled_development(db):
    report = evaluate_research(db)
    assert report["set"] == "development_fake_llm"
    assert "not OpenAI" in report["limitation"]
    assert report["query_count"] == 24
    assert 0 <= report["citation_coverage"] <= 1
    assert 0 <= report["verification_accuracy"] <= 1
    assert report["structured_output_validity"] == 1.0
