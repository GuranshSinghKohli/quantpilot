"""Adversarial probes for step 2b. These assert the section 6 rules, not the current bugs."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.league.local_url import LeagueDatabaseRefused
from tests.unit.league.test_league_checks import _ids, _reject, _seed
from tests.unit.league.test_league_migration import DEFAULT_URL, _config, _engine, _url

INI = Path(__file__).resolve().parents[3] / "alembic_league.ini"


@pytest.fixture
def league_conn():
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    connection = engine.connect()
    transaction = connection.begin()
    try:
        _seed(connection)
        yield connection
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_default_url_points_at_the_local_compose_database() -> None:
    assert DEFAULT_URL == "postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot"


def test_offline_sql_refuses_a_database_that_is_not_quantpilot() -> None:
    cfg = Config(str(INI))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = "postgresql://quantpilot:quantpilot@127.0.0.1:5433/roadsafety"
    with pytest.raises(LeagueDatabaseRefused):
        command.upgrade(cfg, "head", sql=True)


def test_updating_cash_or_qty_below_the_bound_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_portfolios_cash_nonnegative",
        "UPDATE league.portfolios SET cash = -1 WHERE id = :portfolio",
        ids,
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.positions (portfolio_id, ticker, qty, avg_cost)
            VALUES (:portfolio, 'AAPL', 1, 10)
            """
        ),
        ids,
    )
    _reject(
        league_conn,
        "ck_positions_qty_positive",
        """
        UPDATE league.positions SET qty = 0
        WHERE portfolio_id = :portfolio AND ticker = 'AAPL'
        """,
        ids,
    )


def test_sub_penny_negative_cash_is_not_stored_negative(league_conn) -> None:
    ids = _ids(league_conn)
    portfolio_id = str(uuid.uuid4())
    try:
        with league_conn.begin_nested():
            league_conn.execute(
                text(
                    """
                    INSERT INTO league.portfolios (id, agent_id, season_id, kind, cash)
                    VALUES (:id, :agent, :season, 'ghost', -0.00004)
                    """
                ),
                {"id": portfolio_id, **ids},
            )
    except IntegrityError:
        return
    stored = league_conn.execute(
        text("SELECT cash FROM league.portfolios WHERE id = :id"),
        {"id": portfolio_id},
    ).scalar_one()
    assert stored >= 0


def test_half_penny_negative_cash_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_portfolios_cash_nonnegative",
        """
        INSERT INTO league.portfolios (id, agent_id, season_id, kind, cash)
        VALUES (:id, :agent, :season, 'ghost', -0.00005)
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_cash_above_numeric_14_4_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    with pytest.raises(DataError):
        with league_conn.begin_nested():
            league_conn.execute(
                text(
                    """
                    INSERT INTO league.portfolios (id, agent_id, season_id, kind, cash)
                    VALUES (:id, :agent, :season, 'ghost', 10000000000)
                    """
                ),
                {"id": str(uuid.uuid4()), **ids},
            )


def test_duplicate_null_season_achievement_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    league_conn.execute(
        text(
            """
            INSERT INTO league.achievements (id, name, description, xp, icon)
            VALUES ('first-trade', 'First trade', 'Filled once', 10, 'icon')
            """
        )
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.user_achievements (id, player_id, achievement_id, season_id)
            VALUES (:id, :player, 'first-trade', NULL)
            """
        ),
        {"id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "uq_user_achievements_player_achievement_season",
        """
        INSERT INTO league.user_achievements (id, player_id, achievement_id, season_id)
        VALUES (:id, :player, 'first-trade', NULL)
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_two_leagues_may_have_a_null_invite_code(league_conn) -> None:
    ids = _ids(league_conn)
    for _ in range(2):
        league_conn.execute(
            text(
                """
                INSERT INTO league.leagues (id, name, is_public, invite_code, owner_id)
                VALUES (:id, 'Open', true, NULL, :player)
                """
            ),
            {"id": str(uuid.uuid4()), **ids},
        )


def test_second_fill_for_one_order_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    league_conn.execute(
        text(
            """
            INSERT INTO league.fills (id, order_id, price, qty, slippage_bps, bar_time)
            VALUES (:id, :order, 10, 1, 5, TIMESTAMPTZ '2026-01-02 15:00:00+00')
            """
        ),
        {"id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "uq_fills_order_id",
        """
        INSERT INTO league.fills (id, order_id, price, qty, slippage_bps, bar_time)
        VALUES (:id, :order, 11, 1, 5, TIMESTAMPTZ '2026-01-02 15:01:00+00')
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_duplicate_bar_is_rejected(league_conn) -> None:
    params = {"t": "2026-01-02 15:00:00+00"}
    league_conn.execute(
        text(
            """
            INSERT INTO league.bars (ticker, t, o, h, l, c, v, feed)
            VALUES ('AAPL', :t, 1, 2, 1, 2, 10, 'iex')
            """
        ),
        params,
    )
    with pytest.raises(IntegrityError):
        with league_conn.begin_nested():
            league_conn.execute(
                text(
                    """
                    INSERT INTO league.bars (ticker, t, o, h, l, c, v, feed)
                    VALUES ('AAPL', :t, 1, 2, 1, 2, 10, 'iex')
                    """
                ),
                params,
            )


def test_invalid_enums_are_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_seasons_status",
        """
        INSERT INTO league.seasons (id, league_id, number, starts_at, ends_at, status)
        VALUES (:id, :league, 2, TIMESTAMPTZ '2026-01-01 00:00:00+00',
                TIMESTAMPTZ '2026-01-29 00:00:00+00', 'live')
        """,
        {"id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "ck_orders_side",
        """
        INSERT INTO league.orders
            (id, portfolio_id, ticker, side, notional_usd, source, status, game_time)
        VALUES
            (:id, :portfolio, 'AAPL', 'short', 10, 'player', 'accepted',
             TIMESTAMPTZ '2026-01-02 15:00:00+00')
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_omitted_system_flag_defaults_false_and_rejects_empty_news(league_conn) -> None:
    _reject(
        league_conn,
        "ck_rationales_news_or_system",
        """
        INSERT INTO league.rationales (id, cycle_id, summary, confidence, graph_path, validated)
        VALUES (:id, :cycle_id, 'because', 0.5, '["n1"]'::jsonb, true)
        """,
        {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4())},
    )


def test_blank_or_null_news_id_does_not_count_as_news(league_conn) -> None:
    ids = {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4())}
    _reject(
        league_conn,
        "ck_rationales_news_or_system",
        """
        INSERT INTO league.rationales
            (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
        VALUES
            (:id, :cycle_id, 'because', 0.5, ARRAY[''], '["n1"]'::jsonb, true, false)
        """,
        ids,
    )
    _reject(
        league_conn,
        "ck_rationales_news_or_system",
        """
        INSERT INTO league.rationales
            (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
        VALUES
            (:id, :cycle_id, 'because', 0.5, ARRAY[NULL]::text[], '["n1"]'::jsonb, true, false)
        """,
        {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4())},
    )


def test_agent_order_requires_an_existing_rationale(league_conn) -> None:
    """DB rule: an agent order has a rationale_id, and that id is a real row.

    validated = true stays an application rule.
    """
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_orders_agent_rationale",
        """
        INSERT INTO league.orders
            (id, portfolio_id, ticker, side, notional_usd, source, status,
             rationale_id, game_time)
        VALUES
            (:id, :portfolio, 'AAPL', 'buy', 10, 'agent', 'accepted',
             NULL, TIMESTAMPTZ '2026-01-02 15:00:00+00')
        """,
        {"id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "fk_orders_rationale_id",
        """
        INSERT INTO league.orders
            (id, portfolio_id, ticker, side, notional_usd, source, status,
             rationale_id, game_time)
        VALUES
            (:id, :portfolio, 'AAPL', 'buy', 10, 'agent', 'accepted',
             :rationale_id, TIMESTAMPTZ '2026-01-02 15:00:00+00')
        """,
        {"id": str(uuid.uuid4()), "rationale_id": str(uuid.uuid4()), **ids},
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.orders
                (id, portfolio_id, ticker, side, notional_usd, source, status, game_time)
            VALUES
                (:id, :portfolio, 'MSFT', 'buy', 10, 'player', 'accepted',
                 TIMESTAMPTZ '2026-01-02 15:00:00+00')
            """
        ),
        {"id": str(uuid.uuid4()), **ids},
    )
    rationale_id = str(uuid.uuid4())
    league_conn.execute(
        text(
            """
            INSERT INTO league.rationales
                (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
            VALUES
                (:id, :cycle_id, 'because', 0.5, ARRAY['n1'], '["n1"]'::jsonb, false, false)
            """
        ),
        {"id": rationale_id, "cycle_id": str(uuid.uuid4())},
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.orders
                (id, portfolio_id, ticker, side, notional_usd, source, status,
                 rationale_id, game_time)
            VALUES
                (:id, :portfolio, 'NVDA', 'buy', 10, 'agent', 'accepted',
                 :rationale_id, TIMESTAMPTZ '2026-01-02 15:00:00+00')
            """
        ),
        {"id": str(uuid.uuid4()), "rationale_id": rationale_id, **ids},
    )


def test_nonsystem_rationale_rejects_an_empty_graph_path(league_conn) -> None:
    with pytest.raises(IntegrityError):
        with league_conn.begin_nested():
            league_conn.execute(
                text(
                    """
                    INSERT INTO league.rationales
                        (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
                    VALUES
                        (:id, :cycle_id, 'because', 0.5, ARRAY['n1'], '[]'::jsonb, true, false)
                    """
                ),
                {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4())},
            )


def test_snapshot_cash_cannot_go_negative(league_conn) -> None:
    ids = _ids(league_conn)
    with pytest.raises(IntegrityError):
        with league_conn.begin_nested():
            league_conn.execute(
                text(
                    """
                    INSERT INTO league.equity_snapshots (portfolio_id, t, equity, cash)
                    VALUES (:portfolio, TIMESTAMPTZ '2026-01-02 15:00:00+00', 1000, -1)
                    """
                ),
                ids,
            )
    with pytest.raises(IntegrityError):
        with league_conn.begin_nested():
            league_conn.execute(
                text(
                    """
                    INSERT INTO league.equity_snapshots (portfolio_id, t, equity, cash)
                    VALUES (:portfolio, TIMESTAMPTZ '2026-01-02 15:01:00+00', -1, 0)
                    """
                ),
                ids,
            )
