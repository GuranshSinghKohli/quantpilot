"""Drop legacy portfolio briefings and price-alert rules.

Their routes, jobs and agents were removed in commit 350187f. alert_events
stays (material-investigation notifications) without its rule_id link.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adopted pre-Alembic databases may lack some of these objects.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    alert_event_cols = {c["name"] for c in inspector.get_columns("alert_events")}
    if "rule_id" in alert_event_cols:
        alert_event_ix = {ix["name"] for ix in inspector.get_indexes("alert_events")}
        with op.batch_alter_table("alert_events", schema=None) as batch_op:
            if "ix_alert_events_rule_id" in alert_event_ix:
                batch_op.drop_index("ix_alert_events_rule_id")
            batch_op.drop_column("rule_id")

    for table in ("briefings", "alert_rules"):
        if table in tables:
            for ix in inspector.get_indexes(table):
                op.drop_index(ix["name"], table_name=table)
            op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=280), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("alert_rules", schema=None) as batch_op:
        batch_op.create_index("ix_alert_rules_alert_type", ["alert_type"], unique=False)
        batch_op.create_index("ix_alert_rules_ticker", ["ticker"], unique=False)
        batch_op.create_index("ix_alert_rules_user_id", ["user_id"], unique=False)

    op.create_table(
        "briefings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("headline", sa.String(length=280), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("highlights_json", sa.Text(), nullable=False),
        sa.Column("risks_json", sa.Text(), nullable=False),
        sa.Column("watch_tickers_json", sa.Text(), nullable=False),
        sa.Column("holdings_snapshot_json", sa.Text(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("briefings", schema=None) as batch_op:
        batch_op.create_index("ix_briefings_generated_at", ["generated_at"], unique=False)
        batch_op.create_index("ix_briefings_portfolio_id", ["portfolio_id"], unique=False)
        batch_op.create_index("ix_briefings_user_id", ["user_id"], unique=False)

    with op.batch_alter_table("alert_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rule_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "alert_events_rule_id_fkey",
            "alert_rules",
            ["rule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_alert_events_rule_id", ["rule_id"], unique=False)
