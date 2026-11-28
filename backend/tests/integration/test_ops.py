"""Postgres copies of the Step 2.8 status / reconcile / recovery cases."""

import pytest
from sqlalchemy.orm import sessionmaker

from app.pipeline.lake import LocalObjectStore
from tests.integration.test_canonical_load import _truncate
from tests.unit.pipeline.load_env import LoadEnv
from tests.unit.pipeline.test_metrics_and_ops import *  # noqa: F401,F403


@pytest.fixture()
def env(tmp_path, pg_engine):
    _truncate(pg_engine)
    yield LoadEnv(LocalObjectStore(tmp_path / "lake"), sessionmaker(bind=pg_engine, expire_on_commit=False))
    _truncate(pg_engine)
