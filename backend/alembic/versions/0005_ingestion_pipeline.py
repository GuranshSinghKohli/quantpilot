"""Ingestion pipeline: run tracking, lineage, canonical market bars and news.

Tracking: ingestion_sources, ingestion_runs, ingestion_records, processing_jobs.
Canonical: market_bars (new), news_articles (1:1 extension of sources).
companies gains lineage columns (source_ingested_at, event_id, processing_job_id).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 23:09:54.659032
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ingestion_sources',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('domain', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('description', sa.String(length=512), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("domain IN ('news', 'market', 'company')", name='ck_ingestion_sources_domain'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('processing_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_name', sa.String(length=64), nullable=False),
    sa.Column('domain', sa.String(length=16), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('since', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('input_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rejected_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('duplicates_removed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_partitions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('output_uri', sa.String(length=1024), nullable=True),
    sa.Column('manifest_uri', sa.String(length=1024), nullable=True),
    sa.Column('code_version', sa.String(length=64), nullable=True),
    sa.Column('engine_version', sa.String(length=32), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('loaded_rows', sa.Integer(), nullable=True),
    sa.Column('loaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('load_duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("domain IN ('news', 'market', 'company')", name='ck_processing_jobs_domain'),
    sa.CheckConstraint("mode IN ('full', 'incremental')", name='ck_processing_jobs_mode'),
    sa.CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'FAILED')", name='ck_processing_jobs_status'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.create_index('ix_processing_jobs_domain_started', ['domain', 'started_at'], unique=False)

    op.create_table('ingestion_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('trigger', sa.String(length=32), nullable=False),
    sa.Column('params', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('records_received', sa.Integer(), server_default='0', nullable=False),
    sa.Column('records_valid', sa.Integer(), server_default='0', nullable=False),
    sa.Column('records_invalid', sa.Integer(), server_default='0', nullable=False),
    sa.Column('records_duplicate', sa.Integer(), server_default='0', nullable=False),
    sa.Column('records_published', sa.Integer(), server_default='0', nullable=False),
    sa.Column('records_publish_failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')", name='ck_ingestion_runs_status'),
    sa.ForeignKeyConstraint(['source_id'], ['ingestion_sources.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ingestion_runs', schema=None) as batch_op:
        batch_op.create_index('ix_ingestion_runs_source_created', ['source_id', 'created_at'], unique=False)

    op.create_table('ingestion_records',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('domain', sa.String(length=16), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('dedup_key', sa.String(length=300), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('status_reason', sa.Text(), nullable=True),
    sa.Column('kafka_topic', sa.String(length=249), nullable=True),
    sa.Column('kafka_partition', sa.Integer(), nullable=True),
    sa.Column('kafka_offset', sa.BigInteger(), nullable=True),
    sa.Column('raw_uri', sa.String(length=1024), nullable=True),
    sa.Column('processing_job_id', sa.Uuid(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("domain IN ('news', 'market', 'company')", name='ck_ingestion_records_domain'),
    sa.CheckConstraint("status IN ('pending', 'publish_failed', 'published', 'dlq', 'landed', 'processed', 'loaded')", name='ck_ingestion_records_status'),
    sa.ForeignKeyConstraint(['processing_job_id'], ['processing_jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['ingestion_sources.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id')
    )
    with op.batch_alter_table('ingestion_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ingestion_records_dedup_key'), ['dedup_key'], unique=False)
        batch_op.create_index('ix_ingestion_records_run_status', ['run_id', 'status'], unique=False)

    op.create_table('market_bars',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('interval', sa.String(length=8), nullable=False),
    sa.Column('trading_date', sa.Date(), nullable=False),
    sa.Column('bar_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('high', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('low', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('close', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('adj_close', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=True),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('raw_uri', sa.String(length=1024), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('source_ingested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('processing_job_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('low > 0 AND low <= high AND volume >= 0', name='ck_market_bars_values'),
    sa.ForeignKeyConstraint(['event_id'], ['ingestion_records.event_id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['processing_job_id'], ['processing_jobs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'interval', 'trading_date', 'source', name='uq_market_bars_bar')
    )
    with op.batch_alter_table('market_bars', schema=None) as batch_op:
        batch_op.create_index('ix_market_bars_trading_date', ['trading_date'], unique=False)

    op.create_table('news_articles',
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('source_key', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('author', sa.String(length=255), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('tickers', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('source_ingested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('processing_job_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['ingestion_records.event_id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['processing_job_id'], ['processing_jobs.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('source_id')
    )
    with op.batch_alter_table('news_articles', schema=None) as batch_op:
        batch_op.create_index('ix_news_articles_source_external', ['source_key', 'external_id'], unique=False)

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_ingested_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('event_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('processing_job_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_companies_event_id', 'ingestion_records', ['event_id'], ['event_id'], ondelete='RESTRICT')
        batch_op.create_foreign_key('fk_companies_processing_job_id', 'processing_jobs', ['processing_job_id'], ['id'], ondelete='RESTRICT')



def downgrade() -> None:
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_constraint('fk_companies_processing_job_id', type_='foreignkey')
        batch_op.drop_constraint('fk_companies_event_id', type_='foreignkey')
        batch_op.drop_column('processing_job_id')
        batch_op.drop_column('event_id')
        batch_op.drop_column('source_ingested_at')

    with op.batch_alter_table('news_articles', schema=None) as batch_op:
        batch_op.drop_index('ix_news_articles_source_external')

    op.drop_table('news_articles')
    with op.batch_alter_table('market_bars', schema=None) as batch_op:
        batch_op.drop_index('ix_market_bars_trading_date')

    op.drop_table('market_bars')
    with op.batch_alter_table('ingestion_records', schema=None) as batch_op:
        batch_op.drop_index('ix_ingestion_records_run_status')
        batch_op.drop_index(batch_op.f('ix_ingestion_records_dedup_key'))

    op.drop_table('ingestion_records')
    with op.batch_alter_table('ingestion_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_ingestion_runs_source_created')

    op.drop_table('ingestion_runs')
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_processing_jobs_domain_started')

    op.drop_table('processing_jobs')
    op.drop_table('ingestion_sources')
