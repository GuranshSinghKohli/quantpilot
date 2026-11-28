"""DB-derived Prometheus gauges for GET /metrics. Best-effort; never raises."""

from __future__ import annotations

from app.pipeline.metrics import Metrics


def pipeline_gauges(session_factory) -> Metrics:
    metrics = Metrics()
    try:
        from sqlalchemy import func, select

        from app.domain.ingestion_models import IngestionRecord, ProcessingJob

        with session_factory() as db:
            for domain, status, n in db.execute(
                select(IngestionRecord.domain, IngestionRecord.status, func.count())
                .group_by(IngestionRecord.domain, IngestionRecord.status)
            ).all():
                metrics.set("pipeline_records", float(n), domain=domain, status=status)
            for domain, status, n in db.execute(
                select(ProcessingJob.domain, ProcessingJob.status, func.count())
                .group_by(ProcessingJob.domain, ProcessingJob.status)
            ).all():
                metrics.set("pipeline_processing_jobs", float(n), domain=domain, status=status)
            for domain, status, n in db.execute(
                select(ProcessingJob.domain, ProcessingJob.load_status, func.count())
                .where(ProcessingJob.load_status.is_not(None))
                .group_by(ProcessingJob.domain, ProcessingJob.load_status)
            ).all():
                metrics.set("pipeline_canonical_loads", float(n), domain=domain, status=status)
    except Exception:
        return Metrics()
    return metrics
