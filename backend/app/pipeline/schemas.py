"""Export the contracts as JSON Schema into ``docs/schemas/`` for review.

The files are generated, never hand-edited: ``python -m app.pipeline schemas``
rewrites them and ``--check`` fails if they drift from the models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from app.core.settings import REPO_ROOT
from app.pipeline.contracts import (
    EVENT_ADAPTER,
    SCHEMA_VERSION,
    CompanyRecord,
    DeadLetter,
    MarketRecord,
    NewsRecord,
)

SCHEMA_DIR = REPO_ROOT / "docs" / "schemas"


def generate() -> Dict[str, dict]:
    v = SCHEMA_VERSION
    return {
        f"ingestion_event.v{v}.json": EVENT_ADAPTER.json_schema(),
        f"news_record.v{v}.json": NewsRecord.model_json_schema(),
        f"market_record.v{v}.json": MarketRecord.model_json_schema(),
        f"company_record.v{v}.json": CompanyRecord.model_json_schema(),
        f"dead_letter.v{v}.json": DeadLetter.model_json_schema(),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write(directory: Path = SCHEMA_DIR) -> List[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in generate().items():
        path = directory / name
        path.write_text(render(schema), encoding="utf-8")
        paths.append(path)
    return paths


def drift(directory: Path = SCHEMA_DIR) -> List[str]:
    """Names of schema files that are missing or differ from the models."""
    stale = []
    for name, schema in generate().items():
        path = directory / name
        if not path.exists() or path.read_text(encoding="utf-8") != render(schema):
            stale.append(name)
    return stale
