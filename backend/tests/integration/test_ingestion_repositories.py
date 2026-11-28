"""The ingestion repository cases, re-run on PostgreSQL (real ON CONFLICT, JSONB, FKs)."""

import pytest

from tests.unit.pipeline.test_ingestion_repositories import *  # noqa: F401,F403


@pytest.fixture()
def repo_db(db):
    return db
