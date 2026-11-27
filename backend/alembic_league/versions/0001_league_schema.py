"""Create the league schema. Does not alter public.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "league"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS league")

    op.create_table(
        "players",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_id", sa.Text(), nullable=False),
        sa.Column("xp", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["public.users.id"], name="fk_players_user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_players"),
        sa.UniqueConstraint("user_id", name="uq_players_user_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "leagues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("invite_code", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("max_members", sa.Integer(), server_default="50", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["league.players.id"], name="fk_leagues_owner_id"),
        sa.PrimaryKeyConstraint("id", name="pk_leagues"),
        sa.UniqueConstraint("invite_code", name="uq_leagues_invite_code"),
        schema=SCHEMA,
    )
    op.create_table(
        "seasons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("starting_cash", sa.Numeric(14, 4), server_default="1000", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('upcoming', 'active', 'finished')",
            name="ck_seasons_status",
        ),
        sa.ForeignKeyConstraint(["league_id"], ["league.leagues.id"], name="fk_seasons_league_id"),
        sa.PrimaryKeyConstraint("id", name="pk_seasons"),
        schema=SCHEMA,
    )
    op.create_table(
        "league_members",
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["league_id"], ["league.leagues.id"], name="fk_league_members_league_id"),
        sa.ForeignKeyConstraint(["player_id"], ["league.players.id"], name="fk_league_members_player_id"),
        sa.PrimaryKeyConstraint("league_id", "player_id", name="pk_league_members"),
        schema=SCHEMA,
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column(
            "interests",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "excluded_tickers",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "excluded_sectors",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('autopilot', 'approve')", name="ck_agents_mode"),
        sa.ForeignKeyConstraint(["league_id"], ["league.leagues.id"], name="fk_agents_league_id"),
        sa.ForeignKeyConstraint(["player_id"], ["league.players.id"], name="fk_agents_player_id"),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.UniqueConstraint("player_id", "league_id", name="uq_agents_player_league"),
        schema=SCHEMA,
    )
    op.create_table(
        "agent_config_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["league.agents.id"], name="fk_agent_config_history_agent_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_config_history"),
        schema=SCHEMA,
    )
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("cash", sa.Numeric(14, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('actual', 'ghost')", name="ck_portfolios_kind"),
        sa.CheckConstraint("cash >= 0", name="ck_portfolios_cash_nonnegative"),
        sa.ForeignKeyConstraint(["agent_id"], ["league.agents.id"], name="fk_portfolios_agent_id"),
        sa.ForeignKeyConstraint(["season_id"], ["league.seasons.id"], name="fk_portfolios_season_id"),
        sa.PrimaryKeyConstraint("id", name="pk_portfolios"),
        sa.UniqueConstraint("agent_id", "season_id", "kind", name="uq_portfolios_agent_season_kind"),
        schema=SCHEMA,
    )
    op.create_table(
        "positions",
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_cost", sa.Numeric(14, 4), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("qty > 0", name="ck_positions_qty_positive"),
        sa.CheckConstraint("avg_cost >= 0", name="ck_positions_avg_cost_nonnegative"),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["league.portfolios.id"], name="fk_positions_portfolio_id"
        ),
        sa.PrimaryKeyConstraint("portfolio_id", "ticker", name="pk_positions"),
        schema=SCHEMA,
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("notional_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("qty", sa.Numeric(18, 6), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("rationale_id", sa.Uuid(), nullable=True),
        sa.Column("cycle_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("game_time", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_orders_side"),
        sa.CheckConstraint("source IN ('agent', 'player')", name="ck_orders_source"),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'accepted', 'rejected', 'queued', 'filled', 'expired', 'cancelled')",
            name="ck_orders_status",
        ),
        sa.CheckConstraint(
            "(notional_usd IS NULL OR notional_usd > 0)"
            " AND (qty IS NULL OR qty > 0)"
            " AND (notional_usd IS NOT NULL OR qty IS NOT NULL)",
            name="ck_orders_size_positive",
        ),
        sa.CheckConstraint(
            "source <> 'agent' OR rationale_id IS NOT NULL",
            name="ck_orders_agent_rationale",
        ),
        sa.ForeignKeyConstraint(["portfolio_id"], ["league.portfolios.id"], name="fk_orders_portfolio_id"),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        schema=SCHEMA,
    )
    op.create_table(
        "fills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(14, 4), nullable=False),
        sa.Column("qty", sa.Numeric(18, 6), nullable=False),
        sa.Column("slippage_bps", sa.Numeric(6, 2), nullable=False),
        sa.Column("bar_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price > 0", name="ck_fills_price_positive"),
        sa.CheckConstraint("qty > 0", name="ck_fills_qty_positive"),
        sa.ForeignKeyConstraint(["order_id"], ["league.orders.id"], name="fk_fills_order_id"),
        sa.PrimaryKeyConstraint("id", name="pk_fills"),
        sa.UniqueConstraint("order_id", name="uq_fills_order_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "rationales",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column(
            "news_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("graph_path", postgresql.JSONB(), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column("system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rationales_confidence",
        ),
        sa.CheckConstraint(
            "system OR (cardinality(news_ids) > 0"
            " AND array_position(news_ids, NULL) IS NULL"
            " AND NOT ('' = ANY(news_ids)))",
            name="ck_rationales_news_or_system",
        ),
        sa.CheckConstraint(
            "system OR jsonb_array_length(graph_path) > 0",
            name="ck_rationales_graph_or_system",
        ),
        sa.CheckConstraint(
            "system = true OR cycle_id IS NOT NULL",
            name="ck_rationales_cycle_or_system",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rationales"),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_orders_rationale_id",
        "orders",
        "rationales",
        ["rationale_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_table(
        "agent_cycles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(8, 4), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["league.agents.id"], name="fk_agent_cycles_agent_id"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_cycles"),
        schema=SCHEMA,
    )
    op.create_table(
        "equity_snapshots",
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("t", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(14, 4), nullable=False),
        sa.Column("cash", sa.Numeric(14, 4), nullable=False),
        sa.CheckConstraint("cash >= 0", name="ck_equity_snapshots_cash_nonnegative"),
        sa.CheckConstraint("equity >= 0", name="ck_equity_snapshots_equity_nonnegative"),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["league.portfolios.id"], name="fk_equity_snapshots_portfolio_id"
        ),
        sa.PrimaryKeyConstraint("portfolio_id", "t", name="pk_equity_snapshots"),
        schema=SCHEMA,
    )
    op.create_table(
        "achievements",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_achievements"),
        schema=SCHEMA,
    )
    op.create_table(
        "user_achievements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("achievement_id", sa.Text(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["achievement_id"], ["league.achievements.id"], name="fk_user_achievements_achievement_id"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["league.players.id"], name="fk_user_achievements_player_id"
        ),
        sa.ForeignKeyConstraint(
            ["season_id"], ["league.seasons.id"], name="fk_user_achievements_season_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_achievements"),
        sa.UniqueConstraint(
            "player_id",
            "achievement_id",
            "season_id",
            name="uq_user_achievements_player_achievement_season",
            postgresql_nulls_not_distinct=True,
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "bars",
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("t", sa.DateTime(timezone=True), nullable=False),
        sa.Column("o", sa.Numeric(14, 4), nullable=False),
        sa.Column("h", sa.Numeric(14, 4), nullable=False),
        sa.Column("l", sa.Numeric(14, 4), nullable=False),
        sa.Column("c", sa.Numeric(14, 4), nullable=False),
        sa.Column("v", sa.BigInteger(), nullable=False),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ticker", "t", name="pk_bars"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for name in (
        "bars",
        "user_achievements",
        "achievements",
        "equity_snapshots",
        "agent_cycles",
        "fills",
        "orders",
        "rationales",
        "positions",
        "portfolios",
        "agent_config_history",
        "agents",
        "league_members",
        "seasons",
        "leagues",
        "players",
    ):
        op.drop_table(name, schema=SCHEMA)
