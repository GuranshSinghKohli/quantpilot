"""Operational views over the existing pipeline: status, stale jobs, reconcile, recovery.

Read-only except the guarded retry commands, which call the existing runners
after checking prerequisites. No second state machine or lineage system.
"""

from app.pipeline.ops.check import stale_report
from app.pipeline.ops.quality import quality_summary
from app.pipeline.ops.reconcile import reconcile
from app.pipeline.ops.status import pipeline_status

__all__ = ["pipeline_status", "quality_summary", "reconcile", "stale_report"]
