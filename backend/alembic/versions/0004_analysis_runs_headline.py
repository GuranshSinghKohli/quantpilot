"""Rename analysis_runs.recommendation to headline.

Reports no longer carry a buy/hold/sell recommendation; history rows keep the
report title instead. Existing values are kept as they were written.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.alter_column("recommendation", new_column_name="headline")


def downgrade() -> None:
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.alter_column("headline", new_column_name="recommendation")
