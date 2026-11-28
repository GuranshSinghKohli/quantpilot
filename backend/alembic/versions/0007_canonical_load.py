"""Canonical load stage on processing_jobs; full company lineage on companies.

Step 2.7 loads processed Parquet into Postgres. The load stage gets its own
status and counters so processing ``status`` keeps its Step 2.6 meaning, and
companies keep every listed ticker plus source, content hash and raw URI.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 15:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_COUNTERS = ('load_attempts', 'load_candidates', 'load_inserted', 'load_updated', 'load_stale',
             'load_duplicates', 'load_failed')


def upgrade() -> None:
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('load_status', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('load_started_at', sa.DateTime(timezone=True), nullable=True))
        for name in _COUNTERS:
            batch_op.add_column(sa.Column(name, sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('load_partitions', _JSON, server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('load_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('load_report_uri', sa.String(length=1024), nullable=True))
        batch_op.create_check_constraint(
            'ck_processing_jobs_load_status',
            "load_status IN ('CANONICAL_LOAD_RUNNING', 'CANONICAL_LOAD_COMPLETE', 'CANONICAL_LOAD_FAILED')",
        )
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tickers', _JSON, nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('content_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('raw_uri', sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_column('raw_uri')
        batch_op.drop_column('content_hash')
        batch_op.drop_column('source')
        batch_op.drop_column('tickers')
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.drop_constraint('ck_processing_jobs_load_status', type_='check')
        batch_op.drop_column('load_report_uri')
        batch_op.drop_column('load_error')
        batch_op.drop_column('load_partitions')
        for name in reversed(_COUNTERS):
            batch_op.drop_column(name)
        batch_op.drop_column('load_started_at')
        batch_op.drop_column('load_status')
