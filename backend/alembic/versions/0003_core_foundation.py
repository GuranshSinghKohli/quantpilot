"""Core foundation: shared knowledge + owner-scoped research work.

Shared: entities, companies, technologies, sources, evidence_records, events,
event_entities, relationships, relationship_evidence, market_event_windows.
Owner-scoped: research_jobs, agent_runs, research_claims, claim_evidence, reports.
Empty at creation; no production code path writes them in Phase 1.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25 21:19:46.263026
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('entities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('entity_type', sa.String(length=32), nullable=False),
    sa.Column('canonical_name', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("entity_type IN ('company', 'technology', 'organization', 'person')", name='ck_entities_entity_type'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('entity_type', 'canonical_name', name='uq_entities_type_name')
    )
    op.create_table('sources',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('source_type', sa.String(length=32), nullable=False),
    sa.Column('publisher', sa.String(length=255), nullable=True),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('url_hash', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('raw_uri', sa.String(length=1024), nullable=True),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("source_type IN ('news', 'filing', 'press_release', 'research', 'market_data', 'web')", name='ck_sources_source_type'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('url_hash')
    )
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sources_published_at'), ['published_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_sources_source_type'), ['source_type'], unique=False)

    op.create_table('companies',
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('legal_name', sa.String(length=255), nullable=False),
    sa.Column('ticker', sa.String(length=16), nullable=True),
    sa.Column('exchange', sa.String(length=32), nullable=True),
    sa.Column('cik', sa.String(length=10), nullable=True),
    sa.Column('website', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('entity_id'),
    sa.UniqueConstraint('cik'),
    sa.UniqueConstraint('ticker', 'exchange', name='uq_companies_ticker_exchange')
    )
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_companies_ticker'), ['ticker'], unique=False)

    op.create_table('events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('primary_source_id', sa.Uuid(), nullable=True),
    sa.Column('dedup_key', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("event_type IN ('product_launch', 'partnership', 'investment', 'acquisition', 'funding', 'earnings', 'filing', 'regulatory', 'executive_change', 'market_move', 'other')", name='ck_events_event_type'),
    sa.CheckConstraint("status IN ('detected', 'confirmed', 'rejected')", name='ck_events_status'),
    sa.ForeignKeyConstraint(['primary_source_id'], ['sources.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key')
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_events_event_type'), ['event_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_occurred_at'), ['occurred_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_events_primary_source_id'), ['primary_source_id'], unique=False)

    op.create_table('evidence_records',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=False),
    sa.Column('locator', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('extraction_method', sa.String(length=64), nullable=False),
    sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'content_hash', name='uq_evidence_records_source_content')
    )
    with op.batch_alter_table('evidence_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evidence_records_source_id'), ['source_id'], unique=False)

    op.create_table('relationships',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('subject_entity_id', sa.Uuid(), nullable=False),
    sa.Column('relationship_type', sa.String(length=32), nullable=False),
    sa.Column('object_entity_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("relationship_type IN ('INVESTED_IN', 'PARTNERED_WITH', 'SUPPLIES', 'COMPETES_WITH', 'USES', 'ACQUIRED', 'DEVELOPS', 'OWNS', 'CUSTOMERS_OF', 'DEPENDS_ON')", name='ck_relationships_type'),
    sa.CheckConstraint("status IN ('asserted', 'supported', 'disputed', 'retracted')", name='ck_relationships_status'),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name='ck_relationships_confidence'),
    sa.CheckConstraint('subject_entity_id <> object_entity_id', name='ck_relationships_not_self'),
    sa.ForeignKeyConstraint(['object_entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subject_entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('subject_entity_id', 'relationship_type', 'object_entity_id', name='uq_relationships_triple')
    )
    with op.batch_alter_table('relationships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_relationships_object_entity_id'), ['object_entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_relationships_subject_entity_id'), ['subject_entity_id'], unique=False)

    op.create_table('technologies',
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=128), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('entity_id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('event_entities',
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.CheckConstraint("role IN ('subject', 'counterparty', 'mentioned')", name='ck_event_entities_role'),
    sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('event_id', 'entity_id', 'role')
    )
    with op.batch_alter_table('event_entities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_entities_entity_id'), ['entity_id'], unique=False)

    op.create_table('market_event_windows',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('company_entity_id', sa.Uuid(), nullable=False),
    sa.Column('ticker', sa.String(length=16), nullable=False),
    sa.Column('benchmark_ticker', sa.String(length=16), nullable=False),
    sa.Column('window_label', sa.String(length=8), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('return_pct', sa.Float(), nullable=True),
    sa.Column('benchmark_return_pct', sa.Float(), nullable=True),
    sa.Column('abnormal_return_pct', sa.Float(), nullable=True),
    sa.Column('volume_change_pct', sa.Float(), nullable=True),
    sa.Column('volatility', sa.Float(), nullable=True),
    sa.Column('data_source', sa.String(length=64), nullable=False),
    sa.Column('method_version', sa.String(length=32), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("window_label IN ('-1D', '+1D', '+3D', '+5D', '+20D')", name='ck_market_event_windows_label'),
    sa.CheckConstraint('window_start <= window_end', name='ck_market_event_windows_range'),
    sa.ForeignKeyConstraint(['company_entity_id'], ['entities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'ticker', 'window_label', 'method_version', name='uq_market_event_windows_event_ticker_window_method')
    )
    with op.batch_alter_table('market_event_windows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_market_event_windows_company_entity_id'), ['company_entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_market_event_windows_event_id'), ['event_id'], unique=False)

    op.create_table('relationship_evidence',
    sa.Column('relationship_id', sa.Uuid(), nullable=False),
    sa.Column('evidence_id', sa.Uuid(), nullable=False),
    sa.Column('stance', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("stance IN ('supports', 'contradicts', 'context')", name='ck_relationship_evidence_stance'),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence_records.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['relationship_id'], ['relationships.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('relationship_id', 'evidence_id')
    )
    with op.batch_alter_table('relationship_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_relationship_evidence_evidence_id'), ['evidence_id'], unique=False)

    op.create_table('research_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('owner_key', sa.String(length=80), nullable=False),
    sa.Column('job_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('request', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=True),
    sa.Column('current_stage', sa.String(length=64), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("job_type IN ('event_research', 'company_research', 'technology_research')", name='ck_research_jobs_job_type'),
    sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')", name='ck_research_jobs_status'),
    sa.CheckConstraint('attempts >= 0', name='ck_research_jobs_attempts'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_key', 'idempotency_key', name='uq_research_jobs_owner_idempotency')
    )
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_jobs_event_id'), ['event_id'], unique=False)
        batch_op.create_index('ix_research_jobs_owner_created', ['owner_key', 'queued_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_research_jobs_status'), ['status'], unique=False)

    op.create_table('agent_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('research_job_id', sa.Uuid(), nullable=False),
    sa.Column('agent_name', sa.String(length=64), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('prompt_version', sa.String(length=32), nullable=True),
    sa.Column('input_hash', sa.String(length=64), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=True),
    sa.Column('tokens_out', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'skipped')", name='ck_agent_runs_status'),
    sa.CheckConstraint('attempt >= 1', name='ck_agent_runs_attempt'),
    sa.ForeignKeyConstraint(['research_job_id'], ['research_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('research_job_id', 'agent_name', 'attempt', name='uq_agent_runs_job_agent_attempt')
    )
    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_runs_research_job_id'), ['research_job_id'], unique=False)

    op.create_table('reports',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('research_job_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('sections', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'final')", name='ck_reports_status'),
    sa.CheckConstraint('version >= 1', name='ck_reports_version'),
    sa.ForeignKeyConstraint(['research_job_id'], ['research_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('research_job_id', 'version', name='uq_reports_job_version')
    )
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reports_research_job_id'), ['research_job_id'], unique=False)

    op.create_table('research_claims',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('research_job_id', sa.Uuid(), nullable=False),
    sa.Column('agent_run_id', sa.Uuid(), nullable=True),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('claim_kind', sa.String(length=16), nullable=False),
    sa.Column('verification_status', sa.String(length=16), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('uncertainty_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("claim_kind IN ('FACT', 'OBSERVATION', 'INFERENCE', 'SCENARIO')", name='ck_research_claims_kind'),
    sa.CheckConstraint("verification_status IN ('unverified', 'supported', 'uncertain', 'conflicting', 'unsupported')", name='ck_research_claims_verification'),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name='ck_research_claims_confidence'),
    sa.ForeignKeyConstraint(['agent_run_id'], ['agent_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['research_job_id'], ['research_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('research_claims', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_claims_agent_run_id'), ['agent_run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_research_claims_research_job_id'), ['research_job_id'], unique=False)

    op.create_table('claim_evidence',
    sa.Column('claim_id', sa.Uuid(), nullable=False),
    sa.Column('evidence_id', sa.Uuid(), nullable=False),
    sa.Column('stance', sa.String(length=16), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('assessed_by_agent_run_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("stance IN ('supports', 'contradicts', 'context')", name='ck_claim_evidence_stance'),
    sa.ForeignKeyConstraint(['assessed_by_agent_run_id'], ['agent_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['claim_id'], ['research_claims.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence_records.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('claim_id', 'evidence_id')
    )
    with op.batch_alter_table('claim_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_claim_evidence_evidence_id'), ['evidence_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('claim_evidence', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_claim_evidence_evidence_id'))

    op.drop_table('claim_evidence')
    with op.batch_alter_table('research_claims', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_claims_research_job_id'))
        batch_op.drop_index(batch_op.f('ix_research_claims_agent_run_id'))

    op.drop_table('research_claims')
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reports_research_job_id'))

    op.drop_table('reports')
    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_runs_research_job_id'))

    op.drop_table('agent_runs')
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_jobs_status'))
        batch_op.drop_index('ix_research_jobs_owner_created')
        batch_op.drop_index(batch_op.f('ix_research_jobs_event_id'))

    op.drop_table('research_jobs')
    with op.batch_alter_table('relationship_evidence', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_relationship_evidence_evidence_id'))

    op.drop_table('relationship_evidence')
    with op.batch_alter_table('market_event_windows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_market_event_windows_event_id'))
        batch_op.drop_index(batch_op.f('ix_market_event_windows_company_entity_id'))

    op.drop_table('market_event_windows')
    with op.batch_alter_table('event_entities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_entities_entity_id'))

    op.drop_table('event_entities')
    op.drop_table('technologies')
    with op.batch_alter_table('relationships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_relationships_subject_entity_id'))
        batch_op.drop_index(batch_op.f('ix_relationships_object_entity_id'))

    op.drop_table('relationships')
    with op.batch_alter_table('evidence_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evidence_records_source_id'))

    op.drop_table('evidence_records')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_events_primary_source_id'))
        batch_op.drop_index(batch_op.f('ix_events_occurred_at'))
        batch_op.drop_index(batch_op.f('ix_events_event_type'))

    op.drop_table('events')
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_companies_ticker'))

    op.drop_table('companies')
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sources_source_type'))
        batch_op.drop_index(batch_op.f('ix_sources_published_at'))

    op.drop_table('sources')
    op.drop_table('entities')
