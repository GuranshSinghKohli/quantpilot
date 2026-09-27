"""Test wiring for canonical loading without a JVM.

``LoadEnv.processed`` builds a COMPLETED processing job the way Step 2.6 leaves
it: tracked raw events, processed Parquet in the exact processed schema (one
file per partition) and a manifest from the real ``build_manifest``, with the
events moved to ``processed``. The spark tier covers the same path with real
Spark output. SYNTHETIC data only.
"""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import func, select

from app.domain.enums import IngestionRecordStatus, ProcessingJobStatus
from app.domain.ingestion_models import ProcessingJob
from app.pipeline.contracts import SOURCES
from app.pipeline.lake.keys import processed_prefix, raw_key_for
from app.pipeline.load import CanonicalLoadRunner
from app.pipeline.spark.manifest import build_manifest, write_manifest
from app.pipeline.spark.schemas import processed_columns
from app.repositories import ingestion as repo
from tests.unit.pipeline.processing_env import ProcessingEnv

_ARROW = {
    "string": pa.string(), "int": pa.int32(), "bigint": pa.int64(), "boolean": pa.bool_(), "date": pa.date32(),
    "timestamp": pa.timestamp("us", tz="UTC"), "decimal(20,6)": pa.decimal128(20, 6),
    "array<string>": pa.list_(pa.string()),
}
_SIX = Decimal("0.000001")


def arrow_schema(domain: str) -> pa.Schema:
    return pa.schema([pa.field(c.name, _ARROW[c.type]) for c in processed_columns(domain)])


def _price(value) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value)).quantize(_SIX)


@dataclass
class LoadEnv(ProcessingEnv):
    def row(self, event, job_id: uuid.UUID) -> Dict[str, Any]:
        """The processed row Spark writes for ``event`` (Python mirror of the transformers)."""
        spec, rec, key = SOURCES[event.source], event.record, raw_key_for(event)
        row: Dict[str, Any] = {
            "event_id": event.event_id, "source": event.source, "source_type": spec.source_type.value,
            "external_id": event.external_id, "dedup_key": event.dedup_key, "content_hash": event.content_hash,
            "envelope_schema_version": event.schema_version, "run_id": str(event.run_id),
            "correlation_id": event.correlation_id, "ingested_at": event.ingested_at,
            "occurred_at": getattr(event, "occurred_at", None), "raw_key": key, "raw_uri": self.store.uri(key),
            "processing_job_id": str(job_id), "synthetic": spec.synthetic,
            "original_json": json.dumps(event.original, sort_keys=True),
        }
        if event.domain == "news":
            row.update(title=rec.title, url=rec.url, published_at=rec.published_at, author=rec.author,
                       publisher=rec.publisher, summary=rec.summary, tickers=list(rec.tickers))
        elif event.domain == "market":
            row.update(symbol=rec.symbol, interval=rec.interval, trading_date=rec.trading_date,
                       bar_timestamp=rec.timestamp, open=_price(rec.open), high=_price(rec.high), low=_price(rec.low),
                       close=_price(rec.close), adj_close=_price(rec.adj_close), volume=rec.volume,
                       currency=rec.currency)
        else:
            row.update(cik=rec.cik, name=rec.name, ticker=rec.ticker, tickers=list(rec.tickers), exchange=rec.exchange)
        return row

    @staticmethod
    def partition_day(domain: str, row: Dict[str, Any]):
        if domain == "news":
            return row["published_at"].date()
        if domain == "market":
            return row["trading_date"]
        return row["ingested_at"].date()

    def processed(
        self,
        domain: str,
        events: Sequence,
        *,
        land: bool = True,
        mutate: Optional[Callable[[Dict[str, Any]], None]] = None,
        schema: Optional[pa.Schema] = None,
        extra_rows: Sequence[Dict[str, Any]] = (),
        output_rows: Optional[int] = None,
    ) -> uuid.UUID:
        """A COMPLETED processing job whose manifest lists Parquet with ``events``'
        processed rows (``mutate`` edits each row; ``extra_rows`` are appended as-is)."""
        if land:
            self.land(*events)
        job_id = uuid.uuid4()
        started = datetime.now(timezone.utc)
        with self.sessions() as db:
            repo.start_processing_job(db, job_id=job_id, job_name=f"spark.process.{domain}", domain=domain,
                                      mode="incremental")
            db.commit()
        rows = [self.row(e, job_id) for e in events]
        for r in rows:
            if mutate:
                mutate(r)
        rows += list(extra_rows)
        by_day: Dict[Any, List[Dict[str, Any]]] = {}
        for r in rows:
            by_day.setdefault(self.partition_day(domain, r), []).append(r)
        files, partitions = [], []
        for day, part in sorted(by_day.items()):
            partition = f"year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"
            key = f"{processed_prefix(domain)}{partition}/part-00000-{uuid.uuid4()}.c000.snappy.parquet"
            buffer = io.BytesIO()
            pq.write_table(pa.Table.from_pylist(part, schema=schema or arrow_schema(domain)), buffer)
            self.store.put_if_absent(key, buffer.getvalue(), content_type="application/octet-stream")
            files.append(self.store.head(key))
            partitions.append(partition)
        plan = self.plan(domain, objects=[raw_key_for(e) for e in events]) if events else self.plan(domain)
        counts = {"input_objects": len(events), "valid_rows": len(events),
                  "output_rows": len(rows) if output_rows is None else output_rows, "new_rows": len(rows)}
        manifest = build_manifest(store=self.store, job_id=job_id, job_name=f"spark.process.{domain}", plan=plan,
                                  status="COMPLETED", started_at=started, finished_at=datetime.now(timezone.utc),
                                  counts=counts, partitions=partitions, files=files)
        uri = write_manifest(self.store, job_id, manifest)
        with self.sessions() as db:
            repo.advance_records(db, IngestionRecordStatus.PROCESSED, [e.event_id for e in events],
                                 processing_job_id=job_id)
            repo.finish_processing_job(db, db.get(ProcessingJob, job_id), status=ProcessingJobStatus.COMPLETED,
                                       metrics={"output_rows": len(rows), "output_partitions": partitions},
                                       output_uri=self.store.uri(processed_prefix(domain).rstrip("/")),
                                       manifest_uri=uri)
            db.commit()
        return job_id

    def runner(self, **kwargs) -> CanonicalLoadRunner:
        kwargs.setdefault("batch_size", 2)
        return CanonicalLoadRunner(store=self.store, session_factory=self.sessions, **kwargs)

    def load(self, job_id: uuid.UUID, **kwargs):
        since, until = kwargs.pop("since", None), kwargs.pop("until", None)
        return self.runner(**kwargs).run(job_id, since=since, until=until)

    def count(self, model) -> int:
        with self.sessions() as db:
            return db.scalar(select(func.count()).select_from(model))

    def all(self, model, *order) -> list:
        with self.sessions() as db:
            return list(db.scalars(select(model).order_by(*order)))
