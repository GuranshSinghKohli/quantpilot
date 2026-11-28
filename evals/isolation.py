"""Isolated SQLite for memory/alert eval. Never uses the production DATABASE_URL."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

from evals._path import ensure_backend_on_path


@contextmanager
def isolated_db() -> Iterator[object]:
    ensure_backend_on_path()
    fd, path = tempfile.mkstemp(prefix="qp_eval_", suffix=".db")
    os.close(fd)
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db.models import Base

        engine = create_engine(f"sqlite:///{path}", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)
        db = Session()
        try:
            yield db
        finally:
            db.close()
            engine.dispose()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
