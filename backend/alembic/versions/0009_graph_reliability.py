"""Graph reliability: projection run checkpoints and relationship candidates.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27 22:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_projection_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("entities_projected", sa.Integer(), nullable=False),
        sa.Column("relationships_projected", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("code_version", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("operation IN ('project', 'repair')", name="ck_graph_projection_runs_operation"),
        sa.CheckConstraint(
            "status IN ('success', 'partial', 'failed', 'unavailable')",
            name="ck_graph_projection_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "relationship_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_entity_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column("to_entity_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("accepted_relationship_id", sa.Uuid(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "from_entity_id <> to_entity_id",
            name="ck_relationship_candidates_not_self",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_relationship_candidates_confidence",
        ),
        sa.CheckConstraint(
            "relationship_type IN ('INVESTED_IN', 'PARTNERED_WITH', 'SUPPLIES', "
            "'COMPETES_WITH', 'USES', 'ACQUIRED', 'DEVELOPS', 'OWNS', "
            "'CUSTOMERS_OF', 'CUSTOMER_OF', 'DEPENDS_ON')",
            name="ck_relationship_candidates_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'AMBIGUOUS')",
            name="ck_relationship_candidates_status",
        ),
        sa.CheckConstraint(
            "provenance_kind IN ('OBSERVED', 'VERIFIED', 'INFERRED')",
            name="ck_relationship_candidates_provenance",
        ),
        sa.ForeignKeyConstraint(["from_entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["accepted_relationship_id"], ["relationships.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_entity_id",
            "relationship_type",
            "to_entity_id",
            "evidence_id",
            name="uq_relationship_candidates_identity",
        ),
    )
    with op.batch_alter_table("relationship_candidates", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_relationship_candidates_from_entity_id"), ["from_entity_id"])
        batch_op.create_index(batch_op.f("ix_relationship_candidates_to_entity_id"), ["to_entity_id"])
        batch_op.create_index(batch_op.f("ix_relationship_candidates_source_id"), ["source_id"])
        batch_op.create_index(batch_op.f("ix_relationship_candidates_evidence_id"), ["evidence_id"])


def downgrade() -> None:
    with op.batch_alter_table("relationship_candidates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_relationship_candidates_evidence_id"))
        batch_op.drop_index(batch_op.f("ix_relationship_candidates_source_id"))
        batch_op.drop_index(batch_op.f("ix_relationship_candidates_to_entity_id"))
        batch_op.drop_index(batch_op.f("ix_relationship_candidates_from_entity_id"))
    op.drop_table("relationship_candidates")
    op.drop_table("graph_projection_runs")
