"""Evidence retrieval foundation: documents, chunks, embeddings, pgvector.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27 20:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOC_TYPES = (
    "news_article",
    "sec_filing",
    "company_document",
    "research_document",
    "event_document",
    "other",
)
_EVIDENCE_TYPES = (
    "document_excerpt",
    "structured_fact",
    "market_fact",
    "event_fact",
    "graph_fact",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    if postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

        class Vector1536(sa.types.UserDefinedType):
            cache_ok = True

            def get_col_spec(self, **_kw) -> str:
                return "vector(1536)"

        embedding_col = sa.Column("embedding", Vector1536(), nullable=False)
    else:
        embedding_col = sa.Column("embedding", sa.JSON(), nullable=False)

    op.create_table(
        "retrieval_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_content_hash", sa.String(length=64), nullable=True),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"document_type IN ({_in_list(_DOC_TYPES)})", name="ck_retrieval_documents_type"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_retrieval_documents_source"),
        sa.UniqueConstraint("identity_key", name="uq_retrieval_documents_identity"),
    )
    op.create_index("ix_retrieval_documents_published_at", "retrieval_documents", ["published_at"])

    op.create_table(
        "document_entities",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["retrieval_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("document_id", "entity_id"),
    )
    op.create_index("ix_document_entities_entity_id", "document_entities", ["entity_id"])

    op.create_table(
        "retrieval_chunks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("document_content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["retrieval_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "document_content_hash", "chunk_index", name="uq_retrieval_chunks_version"
        ),
    )
    op.create_index("ix_retrieval_chunks_document_id", "retrieval_chunks", ["document_id"])

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        embedding_col,
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["retrieval_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "model_name", "model_version", name="uq_chunk_embeddings_model"),
    )
    op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"])

    with op.batch_alter_table("evidence_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("document_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "evidence_type",
                sa.String(length=32),
                nullable=False,
                server_default="document_excerpt",
            )
        )
        batch_op.create_index(batch_op.f("ix_evidence_records_document_id"), ["document_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_evidence_records_document_id",
            "retrieval_documents",
            ["document_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_evidence_records_type",
            f"evidence_type IN ({_in_list(_EVIDENCE_TYPES)})",
        )


def downgrade() -> None:
    with op.batch_alter_table("evidence_records", schema=None) as batch_op:
        batch_op.drop_constraint("ck_evidence_records_type", type_="check")
        batch_op.drop_constraint("fk_evidence_records_document_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_evidence_records_document_id"))
        batch_op.drop_column("evidence_type")
        batch_op.drop_column("document_id")
    op.drop_index("ix_chunk_embeddings_chunk_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index("ix_retrieval_chunks_document_id", table_name="retrieval_chunks")
    op.drop_table("retrieval_chunks")
    op.drop_index("ix_document_entities_entity_id", table_name="document_entities")
    op.drop_table("document_entities")
    op.drop_index("ix_retrieval_documents_published_at", table_name="retrieval_documents")
    op.drop_table("retrieval_documents")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS vector")
