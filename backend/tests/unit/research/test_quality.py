"""Phase 4.5 fixture checks. Not a production-quality claim."""

from __future__ import annotations

import pytest

from app.core.settings import Settings
from app.research.eval_data import all_cases
from app.research.quality import REQUIRED_COVERAGE, async_checks, configuration_checks, dataset_summary, security_checks
from tests.unit.research.helpers import close_db, db_session


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_dataset_covers_required_case_types():
    summary = dataset_summary()
    assert summary["case_count"] >= 50
    assert summary["baseline_case_count"] == 24
    assert summary["coverage_complete"] is True
    assert REQUIRED_COVERAGE <= {row["case_type"] for row in all_cases()}
    assert "NOT PRODUCTION MODEL QUALITY" in summary["label"]


def test_async_and_security_checks(db):
    async_report = async_checks(db)
    assert async_report["passed"] is True, async_report["checks"]
    security = security_checks(db)
    assert security["passed"] is True, security["checks"]


def test_invalid_research_settings_are_rejected():
    report = configuration_checks()
    assert report["invalid_settings_rejected"] is True
    with pytest.raises(Exception):
        Settings(RESEARCH_MAX_FINDINGS=0)
