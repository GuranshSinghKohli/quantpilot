"""Ingestion pipeline: sources → Kafka → raw lake → Spark → processed Parquet → Postgres.

Orchestration layer (see ``backend/app/README.md``). Records are typed end to end
via ``contracts``; identities are computed once in ``identity`` and carried
downstream so every stage deduplicates on the same keys. Design: docs/PHASE2_PLAN.md.
"""
