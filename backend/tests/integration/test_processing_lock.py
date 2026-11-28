"""One processing job per output prefix: the Postgres advisory lock (no rows written)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.pipeline.spark.jobs.lock import JobAlreadyRunning, output_lock


def test_second_writer_on_the_same_prefix_is_refused(pg_engine):
    sessions = sessionmaker(bind=pg_engine)
    with output_lock(sessions, "processed/news/"):
        with pytest.raises(JobAlreadyRunning, match="processed/news/"):
            with output_lock(sessions, "processed/news/"):
                pass
        with output_lock(sessions, "processed/market/"):  # other prefixes are independent
            pass
    with output_lock(sessions, "processed/news/"):  # released on exit
        pass


def test_lock_is_released_when_the_job_fails(pg_engine):
    sessions = sessionmaker(bind=pg_engine)
    with pytest.raises(RuntimeError):
        with output_lock(sessions, "processed/company/"):
            raise RuntimeError("job failed")
    with output_lock(sessions, "processed/company/"):
        pass
