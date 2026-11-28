"""External source adapters (Step 2.5): fetch + normalize only.

See ``docs/PHASE2_5_PLAN.md``. Adapters never publish, store or touch the
database; ``app.pipeline.ingest.IngestionRunner`` feeds their records to the
existing tracked publisher.
"""
