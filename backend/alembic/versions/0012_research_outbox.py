"""Async research: lease columns and transactional outbox.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-28 12:50:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS = (
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "publish_pending",
)
_STATUS_BEFORE = ("queued", "running", "succeeded", "partial", "failed", "cancelled")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    with op.batch_alter_table("research_jobs") as batch_op:
        batch_op.add_column(sa.Column("error_category", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("correlation_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("worker_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.drop_constraint("ck_research_jobs_status", type_="check")
        batch_op.create_check_constraint("ck_research_jobs_status", f"status IN ({_in_list(_STATUS)})")

    op.create_table(
        "research_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_job_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("topic", sa.String(length=249), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('request', 'completed', 'dlq')", name="ck_research_outbox_kind"),
        sa.CheckConstraint("status IN ('pending', 'published', 'failed')", name="ck_research_outbox_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_research_outbox_attempts"),
        sa.ForeignKeyConstraint(["research_job_id"], ["research_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_research_outbox_event_id"),
    )
    op.create_index("ix_research_outbox_research_job_id", "research_outbox", ["research_job_id"])
    op.create_index("ix_research_outbox_pending", "research_outbox", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_research_outbox_pending", table_name="research_outbox")
    op.drop_index("ix_research_outbox_research_job_id", table_name="research_outbox")
    op.drop_table("research_outbox")
    with op.batch_alter_table("research_jobs") as batch_op:
        batch_op.drop_constraint("ck_research_jobs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_research_jobs_status", f"status IN ({_in_list(_STATUS_BEFORE)})"
        )
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("lease_until")
        batch_op.drop_column("worker_id")
        batch_op.drop_column("correlation_id")
        batch_op.drop_column("error_category")
