"""processing_jobs: input window end, input prefix, input object count, Spark app id.

Step 2.6 processing jobs select raw objects by ingestion-date window, raw key
prefix or explicit list; 0005 only had ``since``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-27 13:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('until', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('input_prefix', sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column('input_objects', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('spark_app_id', sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.drop_column('spark_app_id')
        batch_op.drop_column('input_objects')
        batch_op.drop_column('input_prefix')
        batch_op.drop_column('until')
