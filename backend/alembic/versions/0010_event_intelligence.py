"""Event intelligence: candidates, impacts, event provenance, graph event kinds.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27 23:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVENT_TYPES = (
    "news",
    "product_launch",
    "technology_release",
    "partnership",
    "investment",
    "acquisition",
    "funding",
    "earnings",
    "filing",
    "regulatory",
    "executive_change",
    "market_move",
    "other",
)
_ROLES = (
    "subject",
    "counterparty",
    "target",
    "acquirer",
    "acquired",
    "investor",
    "partner",
    "supplier",
    "customer",
    "technology",
    "mentioned",
    "other",
)
_CANDIDATE_STATUSES = ("PENDING", "ACCEPTED", "REJECTED", "AMBIGUOUS")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("evidence_id", sa.Uuid(), nullable=True))
        batch_op.create_index(batch_op.f("ix_events_evidence_id"), ["evidence_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_events_evidence_id",
            "evidence_records",
            ["evidence_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_constraint("ck_events_event_type", type_="check")
        batch_op.create_check_constraint("ck_events_event_type", f"event_type IN ({_in_list(_EVENT_TYPES)})")

    with op.batch_alter_table("event_entities", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("evidence_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch_op.create_foreign_key(
            "fk_event_entities_source_id",
            "sources",
            ["source_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_event_entities_evidence_id",
            "evidence_records",
            ["evidence_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_constraint("ck_event_entities_role", type_="check")
        batch_op.create_check_constraint("ck_event_entities_role", f"role IN ({_in_list(_ROLES)})")
        batch_op.create_check_constraint(
            "ck_event_entities_confidence",
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
        )

    with op.batch_alter_table("graph_projections", schema=None) as batch_op:
        batch_op.drop_constraint("ck_graph_projections_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_graph_projections_kind",
            "record_kind IN ('entity', 'relationship', 'event', 'event_link')",
        )

    with op.batch_alter_table("graph_projection_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("events_projected", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("event_links_projected", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "event_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("accepted_event_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_event_candidates_confidence",
        ),
        sa.CheckConstraint(f"event_type IN ({_in_list(_EVENT_TYPES)})", name="ck_event_candidates_event_type"),
        sa.CheckConstraint(f"status IN ({_in_list(_CANDIDATE_STATUSES)})", name="ck_event_candidates_status"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["accepted_event_id"], ["events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_key", name="uq_event_candidates_identity"),
    )
    with op.batch_alter_table("event_candidates", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_event_candidates_source_id"), ["source_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_event_candidates_evidence_id"), ["evidence_id"], unique=False)

    op.create_table(
        "event_impacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("event_trading_date", sa.Date(), nullable=True),
        sa.Column("baseline_price", sa.Float(), nullable=True),
        sa.Column("return_m1", sa.Float(), nullable=True),
        sa.Column("return_p1", sa.Float(), nullable=True),
        sa.Column("return_p3", sa.Float(), nullable=True),
        sa.Column("return_p5", sa.Float(), nullable=True),
        sa.Column("return_p20", sa.Float(), nullable=True),
        sa.Column("benchmark_return_p1", sa.Float(), nullable=True),
        sa.Column("benchmark_return_p3", sa.Float(), nullable=True),
        sa.Column("benchmark_return_p5", sa.Float(), nullable=True),
        sa.Column("benchmark_return_p20", sa.Float(), nullable=True),
        sa.Column("relative_return_p1", sa.Float(), nullable=True),
        sa.Column("relative_return_p3", sa.Float(), nullable=True),
        sa.Column("relative_return_p5", sa.Float(), nullable=True),
        sa.Column("relative_return_p20", sa.Float(), nullable=True),
        sa.Column("event_day_volume", sa.Float(), nullable=True),
        sa.Column("pre_event_average_volume", sa.Float(), nullable=True),
        sa.Column("volume_ratio", sa.Float(), nullable=True),
        sa.Column("pre_event_volatility", sa.Float(), nullable=True),
        sa.Column("event_window_volatility", sa.Float(), nullable=True),
        sa.Column("first_market_date", sa.Date(), nullable=True),
        sa.Column("last_market_date", sa.Date(), nullable=True),
        sa.Column("calculation_version", sa.String(length=16), nullable=False),
        sa.Column("calculation_job_id", sa.Uuid(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "entity_id",
            "symbol",
            "calculation_version",
            name="uq_event_impacts_identity",
        ),
    )
    with op.batch_alter_table("event_impacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_event_impacts_entity"), ["entity_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_event_impacts_event"), ["event_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("event_impacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_impacts_event"))
        batch_op.drop_index(batch_op.f("ix_event_impacts_entity"))
    op.drop_table("event_impacts")

    with op.batch_alter_table("event_candidates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_event_candidates_evidence_id"))
        batch_op.drop_index(batch_op.f("ix_event_candidates_source_id"))
    op.drop_table("event_candidates")

    with op.batch_alter_table("graph_projection_runs", schema=None) as batch_op:
        batch_op.drop_column("event_links_projected")
        batch_op.drop_column("events_projected")

    with op.batch_alter_table("graph_projections", schema=None) as batch_op:
        batch_op.drop_constraint("ck_graph_projections_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_graph_projections_kind",
            "record_kind IN ('entity', 'relationship')",
        )

    with op.batch_alter_table("event_entities", schema=None) as batch_op:
        batch_op.drop_constraint("ck_event_entities_confidence", type_="check")
        batch_op.drop_constraint("ck_event_entities_role", type_="check")
        batch_op.create_check_constraint(
            "ck_event_entities_role",
            "role IN ('subject', 'counterparty', 'mentioned')",
        )
        batch_op.drop_constraint("fk_event_entities_evidence_id", type_="foreignkey")
        batch_op.drop_constraint("fk_event_entities_source_id", type_="foreignkey")
        batch_op.drop_column("confidence")
        batch_op.drop_column("evidence_id")
        batch_op.drop_column("source_id")

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_constraint("ck_events_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_events_event_type",
            "event_type IN ('product_launch', 'partnership', 'investment', 'acquisition', "
            "'funding', 'earnings', 'filing', 'regulatory', 'executive_change', 'market_move', 'other')",
        )
        batch_op.drop_constraint("fk_events_evidence_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_events_evidence_id"))
        batch_op.drop_column("evidence_id")
        batch_op.drop_column("published_at")
