"""League tables. Metadata is schema `league` only, so it cannot map `public`.

`agent_config_history.id` is a UUID primary key. The spec names the column `id`
without a type; every other League id is a UUID. `seasons.starting_cash` is
NUMERIC(14,4) because section 6 says all money uses that precision. The sketch
writes it as NUMERIC DEFAULT 1000.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class LeagueBase(DeclarativeBase):
    metadata = MetaData(schema="league")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Player(LeagueBase):
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("public.users.id"), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_id: Mapped[str] = mapped_column(Text, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _ts()


class League(LeagueBase):
    __tablename__ = "leagues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False)
    invite_code: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("players.id"), nullable=False)
    max_members: Mapped[int] = mapped_column(Integer, nullable=False, server_default="50")
    created_at: Mapped[datetime] = _ts()


class Season(LeagueBase):
    __tablename__ = "seasons"
    __table_args__ = (
        CheckConstraint(
            "status IN ('upcoming', 'active', 'finished')",
            name="ck_seasons_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    league_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("leagues.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    starting_cash: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, server_default="1000"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)


class LeagueMember(LeagueBase):
    __tablename__ = "league_members"

    league_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leagues.id"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("players.id"), primary_key=True
    )
    joined_at: Mapped[datetime] = _ts()


class Agent(LeagueBase):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("player_id", "league_id", name="uq_agents_player_league"),
        CheckConstraint("mode IN ('autopilot', 'approve')", name="ck_agents_mode"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    player_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("players.id"), nullable=False)
    league_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("leagues.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    style: Mapped[str] = mapped_column(Text, nullable=False)
    interests: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False)
    excluded_tickers: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False)
    excluded_sectors: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _ts()


class AgentConfigHistory(LeagueBase):
    __tablename__ = "agent_config_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agents.id"), nullable=False)
    config: Mapped[Any] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Portfolio(LeagueBase):
    __tablename__ = "portfolios"
    __table_args__ = (
        UniqueConstraint("agent_id", "season_id", "kind", name="uq_portfolios_agent_season_kind"),
        CheckConstraint("kind IN ('actual', 'ghost')", name="ck_portfolios_kind"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agents.id"), nullable=False)
    season_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("seasons.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    created_at: Mapped[datetime] = _ts()


class Position(LeagueBase):
    __tablename__ = "positions"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id"), primary_key=True
    )
    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    updated_at: Mapped[datetime] = _ts()


class Order(LeagueBase):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="ck_orders_side"),
        CheckConstraint("source IN ('agent', 'player')", name="ck_orders_source"),
        CheckConstraint(
            "status IN ('pending_approval', 'accepted', 'rejected', 'queued', 'filled', 'expired', 'cancelled')",
            name="ck_orders_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    notional_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    cycle_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = _ts()
    game_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Fill(LeagueBase):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orders.id"), unique=True, nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime] = _ts()


class Rationale(LeagueBase):
    __tablename__ = "rationales"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cycle_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    news_ids: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False)
    graph_path: Mapped[Any] = mapped_column(JSONB, nullable=False)
    validated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _ts()


class AgentCycle(LeagueBase):
    __tablename__ = "agent_cycles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agents.id"), nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EquitySnapshot(LeagueBase):
    __tablename__ = "equity_snapshots"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("portfolios.id"), primary_key=True
    )
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)


class Achievement(LeagueBase):
    __tablename__ = "achievements"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    icon: Mapped[str] = mapped_column(Text, nullable=False)


class UserAchievement(LeagueBase):
    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "achievement_id",
            "season_id",
            name="uq_user_achievements_player_achievement_season",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    player_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("players.id"), nullable=False)
    achievement_id: Mapped[str] = mapped_column(
        Text, ForeignKey("achievements.id"), nullable=False
    )
    season_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("seasons.id"), nullable=True
    )
    unlocked_at: Mapped[datetime] = _ts()


class Bar(LeagueBase):
    __tablename__ = "bars"

    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    o: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    h: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    l: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    c: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    v: Mapped[int] = mapped_column(BigInteger, nullable=False)
    feed: Mapped[str] = mapped_column(Text, nullable=False)
