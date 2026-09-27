"""Disposable Neo4j tier. Skip unless TEST_NEO4J_URI is set."""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from app.core.settings import get_settings

TEST_NEO4J_URI = os.getenv("TEST_NEO4J_URI", "").strip()
TEST_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "quantpilot-dev-only")


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="TEST_NEO4J_URI not set")
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.neo4j)
            if not TEST_NEO4J_URI:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def neo4j_settings() -> Iterator[None]:
    if not TEST_NEO4J_URI:
        pytest.skip("TEST_NEO4J_URI not set")
    os.environ["NEO4J_URI"] = TEST_NEO4J_URI
    os.environ["NEO4J_PASSWORD"] = TEST_NEO4J_PASSWORD
    os.environ["NEO4J_USER"] = os.getenv("NEO4J_USER", "neo4j")
    get_settings.cache_clear()
    from app.graph.store import close_driver

    yield
    close_driver()
    get_settings.cache_clear()
