"""Canonical load failures. Each one ends the attempt as CANONICAL_LOAD_FAILED."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.pipeline.errors import PipelineError


class LoadError(PipelineError):
    pass


class LoadConfigError(LoadError, ValueError):
    """Bad selection or a job/manifest that cannot be loaded (CLI exit 2)."""


class ManifestMismatch(LoadError):
    """The manifest does not describe what is in the lake (missing or changed files)."""


@dataclass(frozen=True)
class RowProblem:
    file: str
    row: int  # 0-based row number inside the file
    event_id: Optional[str]
    reason: str

    def as_dict(self) -> dict:
        return {"file": self.file, "row": self.row, "event_id": self.event_id, "reason": self.reason}

    def __str__(self) -> str:
        return f"{self.file}#{self.row} event {self.event_id or '?'}: {self.reason}"


class LoadValidationError(LoadError):
    """Processed rows failed validation; nothing was written."""

    def __init__(self, problems: List[RowProblem]):
        self.problems = problems
        shown = "; ".join(str(p) for p in problems[:50])
        more = f" (+{len(problems) - 50} more)" if len(problems) > 50 else ""
        super().__init__(f"{len(problems)} invalid processed row(s): {shown}{more}")


class ReconciliationError(LoadError):
    """Load counts do not add up; the attempt is marked failed."""
