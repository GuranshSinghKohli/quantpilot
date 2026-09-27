"""Graph foundation: entity identity columns, aliases, provenance, projection log.

Reuses entities + relationships (no parallel graph_* business tables).
Adds product/event types, CUSTOMER_OF, provenance_kind, entity_aliases,
graph_projections, and backfills SEC company identity.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27 21:00:00
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PUNCT = re.compile(r"[^\w\s&/+-]", re.UNICODE)


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", (value or "").strip()).casefold()
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def upgrade() -> None:
    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.add_column(sa.Column("normalized_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("source_system", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("source_key", sa.String(length=128), nullable=True))
        batch_op.drop_constraint("ck_entities_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_entities_entity_type",
            "entity_type IN ('company', 'technology', 'organization', 'person', 'product', 'event')",
        )

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, canonical_name FROM entities")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE entities SET normalized_name = :n WHERE id = :id"),
            {"n": _normalize_name(row[1]), "id": row[0]},
        )
    companies = bind.execute(
        sa.text("SELECT entity_id, cik, source FROM companies WHERE cik IS NOT NULL")
    ).fetchall()
    for entity_id, cik, source in companies:
        system = "sec" if not source or str(source).lower().startswith("sec") else str(source)
        bind.execute(
            sa.text(
                "UPDATE entities SET source_system = :sys, source_key = :key "
                "WHERE id = :id AND source_key IS NULL"
            ),
            {"sys": system, "key": cik, "id": entity_id},
        )

    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.alter_column("normalized_name", existing_type=sa.String(length=255), nullable=False)
        batch_op.create_index("ix_entities_normalized_name", ["normalized_name"], unique=False)

    op.create_index(
        "uq_entities_source_identity",
        "entities",
        ["source_system", "source_key"],
        unique=True,
        sqlite_where=sa.text("source_system IS NOT NULL AND source_key IS NOT NULL"),
        postgresql_where=sa.text("source_system IS NOT NULL AND source_key IS NOT NULL"),
    )

    with op.batch_alter_table("relationships", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "provenance_kind",
                sa.String(length=16),
                nullable=False,
                server_default="OBSERVED",
            )
        )
        batch_op.drop_constraint("ck_relationships_type", type_="check")
        batch_op.create_check_constraint(
            "ck_relationships_type",
            "relationship_type IN ('INVESTED_IN', 'PARTNERED_WITH', 'SUPPLIES', "
            "'COMPETES_WITH', 'USES', 'ACQUIRED', 'DEVELOPS', 'OWNS', "
            "'CUSTOMERS_OF', 'CUSTOMER_OF', 'DEPENDS_ON')",
        )
        batch_op.create_check_constraint(
            "ck_relationships_valid_range",
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
        )
        batch_op.create_check_constraint(
            "ck_relationships_provenance",
            "provenance_kind IN ('OBSERVED', 'VERIFIED', 'INFERRED')",
        )

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "alias_type IN ('LEGAL_NAME', 'DISPLAY_NAME', 'TICKER', 'FORMER_NAME', "
            "'PRODUCT_ALIAS', 'TECHNOLOGY_ALIAS', 'SOURCE_ALIAS')",
            name="ck_entity_aliases_type",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_entity_aliases_confidence"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id", "alias_type", "normalized_alias", name="uq_entity_aliases_entity_type_norm"
        ),
    )
    with op.batch_alter_table("entity_aliases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_entity_aliases_entity_id"), ["entity_id"], unique=False)

    alias_rows = bind.execute(
        sa.text(
            "SELECT e.id, e.canonical_name, c.legal_name, c.ticker, c.tickers "
            "FROM entities e JOIN companies c ON c.entity_id = e.id"
        )
    ).fetchall()
    insert_alias = sa.text(
        "INSERT INTO entity_aliases (id, entity_id, alias, normalized_alias, alias_type, confidence) "
        "VALUES (:id, :entity_id, :alias, :normalized_alias, :alias_type, 1.0)"
    )
    seen = set()
    for entity_id, canonical, legal, ticker, tickers in alias_rows:
        names = []
        if legal:
            names.append(("LEGAL_NAME", legal))
        if canonical:
            names.append(("DISPLAY_NAME", canonical))
        symbols = []
        if ticker:
            symbols.append(ticker)
        if isinstance(tickers, list):
            symbols.extend(str(t) for t in tickers)
        elif isinstance(tickers, str) and tickers.startswith("["):
            import json

            try:
                symbols.extend(str(t) for t in json.loads(tickers))
            except json.JSONDecodeError:
                pass
        for kind, raw in names:
            norm = _normalize_name(raw)
            key = (str(entity_id), kind, norm)
            if not norm or key in seen:
                continue
            seen.add(key)
            bind.execute(
                insert_alias,
                {
                    "id": _uuid4(),
                    "entity_id": entity_id,
                    "alias": raw,
                    "normalized_alias": norm,
                    "alias_type": kind,
                },
            )
        for symbol in symbols:
            norm = symbol.strip().upper()
            key = (str(entity_id), "TICKER", norm)
            if not norm or key in seen:
                continue
            seen.add(key)
            bind.execute(
                insert_alias,
                {
                    "id": _uuid4(),
                    "entity_id": entity_id,
                    "alias": symbol.strip().upper(),
                    "normalized_alias": norm,
                    "alias_type": "TICKER",
                },
            )

    op.create_table(
        "graph_projections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("record_kind", sa.String(length=16), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("projected_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("record_kind IN ('entity', 'relationship')", name="ck_graph_projections_kind"),
        sa.CheckConstraint("status IN ('success', 'failed')", name="ck_graph_projections_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_kind", "record_id", name="uq_graph_projections_record"),
    )


def _uuid4() -> str:
    import uuid

    return str(uuid.uuid4())


def downgrade() -> None:
    op.drop_table("graph_projections")
    with op.batch_alter_table("entity_aliases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_entity_aliases_entity_id"))
    op.drop_table("entity_aliases")
    with op.batch_alter_table("relationships", schema=None) as batch_op:
        batch_op.drop_constraint("ck_relationships_provenance", type_="check")
        batch_op.drop_constraint("ck_relationships_valid_range", type_="check")
        batch_op.drop_constraint("ck_relationships_type", type_="check")
        batch_op.create_check_constraint(
            "ck_relationships_type",
            "relationship_type IN ('INVESTED_IN', 'PARTNERED_WITH', 'SUPPLIES', "
            "'COMPETES_WITH', 'USES', 'ACQUIRED', 'DEVELOPS', 'OWNS', "
            "'CUSTOMERS_OF', 'DEPENDS_ON')",
        )
        batch_op.drop_column("provenance_kind")
    op.drop_index("uq_entities_source_identity", table_name="entities")
    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.drop_index("ix_entities_normalized_name")
        batch_op.drop_constraint("ck_entities_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_entities_entity_type",
            "entity_type IN ('company', 'technology', 'organization', 'person')",
        )
        batch_op.drop_column("source_key")
        batch_op.drop_column("source_system")
        batch_op.drop_column("normalized_name")
