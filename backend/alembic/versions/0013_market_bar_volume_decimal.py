"""Store Massive daily volume as DECIMAL(20,6).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-03 13:45:00

The provider's aggregate ``v`` is a fractional float. A bigint column cannot
keep that value, and rounding it to a whole share would invent a count.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE market_bars ALTER COLUMN volume TYPE NUMERIC(20,6) USING volume::numeric(20,6)"
        )
        return
    with op.batch_alter_table("market_bars") as batch:
        batch.alter_column(
            "volume",
            existing_type=sa.BigInteger(),
            type_=sa.Numeric(20, 6),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE market_bars ALTER COLUMN volume TYPE BIGINT USING round(volume)::bigint"
        )
        return
    with op.batch_alter_table("market_bars") as batch:
        batch.alter_column(
            "volume",
            existing_type=sa.Numeric(20, 6),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
