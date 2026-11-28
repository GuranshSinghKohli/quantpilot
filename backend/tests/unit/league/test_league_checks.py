"""Postgres rejects rows that break the league money and rationale checks."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit.league.test_league_migration import _config, _engine, _url
from alembic import command


def _seed(conn) -> None:
    user_id = conn.execute(
        text(
            """
            INSERT INTO public.users (email, hashed_password, created_at)
            VALUES (:email, 'not-a-login', now())
            RETURNING id
            """
        ),
        {"email": f"league-check-{uuid.uuid4()}@local.quantpilot"},
    ).scalar_one()
    ids = {
        "user_id": user_id,
        "player": str(uuid.uuid4()),
        "league": str(uuid.uuid4()),
        "season": str(uuid.uuid4()),
        "agent": str(uuid.uuid4()),
        "portfolio": str(uuid.uuid4()),
        "order": str(uuid.uuid4()),
    }
    conn.execute(
        text(
            """
            INSERT INTO league.players (id, user_id, display_name, avatar_id)
            VALUES (:player, :user_id, 'Check', 'avatar')
            """
        ),
        ids,
    )
    conn.execute(
        text(
            """
            INSERT INTO league.leagues (id, name, is_public, owner_id)
            VALUES (:league, 'Check', false, :player)
            """
        ),
        ids,
    )
    conn.execute(
        text(
            """
            INSERT INTO league.seasons (id, league_id, number, starts_at, ends_at, status)
            VALUES (:season, :league, 1, now(), now() + interval '28 days', 'active')
            """
        ),
        ids,
    )
    conn.execute(
        text(
            """
            INSERT INTO league.agents
                (id, player_id, league_id, name, risk_level, style, mode, config_version)
            VALUES
                (:agent, :player, :league, 'Check', 'balanced', 'momentum', 'autopilot', 1)
            """
        ),
        ids,
    )
    conn.execute(
        text(
            """
            INSERT INTO league.portfolios (id, agent_id, season_id, kind, cash)
            VALUES (:portfolio, :agent, :season, 'actual', 1000)
            """
        ),
        ids,
    )
    conn.execute(
        text(
            """
            INSERT INTO league.orders
                (id, portfolio_id, ticker, side, notional_usd, source, status, game_time)
            VALUES
                (:order, :portfolio, 'AAPL', 'buy', 10, 'player', 'accepted', now())
            """
        ),
        ids,
    )
    conn.info["league_ids"] = ids


def _ids(conn) -> dict:
    return conn.info["league_ids"]


def _reject(conn, constraint: str, statement: str, params: dict) -> None:
    with pytest.raises(IntegrityError) as caught:
        with conn.begin_nested():
            conn.execute(text(statement), params)
    assert constraint in str(caught.value)


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


def test_negative_cash_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_portfolios_cash_nonnegative",
        """
        INSERT INTO league.portfolios (id, agent_id, season_id, kind, cash)
        VALUES (:id, :agent, :season, 'ghost', -0.0001)
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_zero_and_negative_position_qty_are_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    for qty in ("0", "-1"):
        _reject(
            league_conn,
            "ck_positions_qty_positive",
            """
            INSERT INTO league.positions (portfolio_id, ticker, qty, avg_cost, cost_basis)
            VALUES (:portfolio, :ticker, :qty, 10, 10)
            """,
            {"ticker": f"Q{qty}", "qty": qty, **ids},
        )


def test_negative_avg_cost_is_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_positions_avg_cost_nonnegative",
        """
        INSERT INTO league.positions (portfolio_id, ticker, qty, avg_cost, cost_basis)
        VALUES (:portfolio, 'AAPL', 1, -0.0001, 10)
        """,
        ids,
    )


def test_non_positive_fill_price_and_qty_are_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_fills_price_positive",
        """
        INSERT INTO league.fills (id, order_id, price, qty, slippage_bps, bar_time)
        VALUES (:id, :order, 0, 1, 5, now())
        """,
        {"id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "ck_fills_qty_positive",
        """
        INSERT INTO league.fills (id, order_id, price, qty, slippage_bps, bar_time)
        VALUES (:id, :order, 10, 0, 5, now())
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_order_size_must_be_present_and_positive(league_conn) -> None:
    ids = _ids(league_conn)
    base = """
        INSERT INTO league.orders
            (id, portfolio_id, ticker, side, notional_usd, qty, source, status, game_time)
        VALUES
            (:id, :portfolio, 'AAPL', 'buy', :notional, :qty, 'player', 'accepted', now())
    """
    _reject(
        league_conn,
        "ck_orders_size_positive",
        base,
        {"id": str(uuid.uuid4()), "notional": None, "qty": None, **ids},
    )
    _reject(
        league_conn,
        "ck_orders_size_positive",
        base,
        {"id": str(uuid.uuid4()), "notional": 0, "qty": None, **ids},
    )
    _reject(
        league_conn,
        "ck_orders_size_positive",
        base,
        {"id": str(uuid.uuid4()), "notional": None, "qty": -1, **ids},
    )


def test_rationale_confidence_news_and_cycle_are_rejected(league_conn) -> None:
    ids = _ids(league_conn)
    _reject(
        league_conn,
        "ck_rationales_confidence",
        """
        INSERT INTO league.rationales
            (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
        VALUES
            (:id, :cycle_id, 'because', -0.01, ARRAY['n1'], '["n1"]'::jsonb, true, false)
        """,
        {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "ck_rationales_confidence",
        """
        INSERT INTO league.rationales
            (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
        VALUES
            (:id, :cycle_id, 'because', 1.01, ARRAY['n1'], '["n1"]'::jsonb, true, false)
        """,
        {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "ck_rationales_news_or_system",
        """
        INSERT INTO league.rationales
            (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
        VALUES
            (:id, :cycle_id, 'because', 0.50, '{}'::text[], '["n1"]'::jsonb, true, false)
        """,
        {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4()), **ids},
    )
    _reject(
        league_conn,
        "ck_rationales_cycle_or_system",
        """
        INSERT INTO league.rationales
            (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
        VALUES
            (:id, NULL, 'because', 0.50, ARRAY['n1'], '["n1"]'::jsonb, true, false)
        """,
        {"id": str(uuid.uuid4()), **ids},
    )


def test_allowed_edges_and_column_defaults(league_conn) -> None:
    ids = _ids(league_conn)
    league_conn.execute(
        text(
            """
            INSERT INTO league.portfolios (id, agent_id, season_id, kind, cash)
            VALUES (:id, :agent, :season, 'ghost', 0)
            """
        ),
        {"id": str(uuid.uuid4()), **ids},
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.positions (portfolio_id, ticker, qty, avg_cost, cost_basis)
            VALUES (:portfolio, 'MSFT', 0.000001, 0, 0)
            """
        ),
        ids,
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.orders
                (id, portfolio_id, ticker, side, qty, source, status, game_time)
            VALUES
                (:id, :portfolio, 'MSFT', 'buy', 1, 'player', 'accepted', now())
            """
        ),
        {"id": str(uuid.uuid4()), **ids},
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.rationales
                (id, summary, confidence, graph_path, validated, system)
            VALUES
                (:id, 'Stop-loss at -12%', 0, '[]'::jsonb, true, true)
            """
        ),
        {"id": str(uuid.uuid4())},
    )
    league_conn.execute(
        text(
            """
            INSERT INTO league.rationales
                (id, cycle_id, summary, confidence, news_ids, graph_path, validated, system)
            VALUES
                (:id, :cycle_id, 'because', 1, ARRAY['n1'], '["n1"]'::jsonb, true, false)
            """
        ),
        {"id": str(uuid.uuid4()), "cycle_id": str(uuid.uuid4())},
    )
    cycle_id = str(uuid.uuid4())
    league_conn.execute(
        text(
            """
            INSERT INTO league.agent_cycles (id, agent_id, trigger, started_at, status)
            VALUES (:id, :agent, 'manual', now(), 'running')
            """
        ),
        {"id": cycle_id, **ids},
    )
    row = league_conn.execute(
        text(
            """
            SELECT finished_at, tokens_in, tokens_out, cost_usd, latency_ms,
                   proposals_made, proposals_rejected, proposals_dropped,
                   rejection_reasons, drop_reasons
            FROM league.agent_cycles WHERE id = :id
            """
        ),
        {"id": cycle_id},
    ).one()
    assert row.finished_at is None
    assert row.tokens_in == 0
    assert row.tokens_out == 0
    assert row.cost_usd == 0
    assert row.latency_ms is None
    assert row.proposals_made == 0
    assert row.proposals_rejected == 0
    assert row.proposals_dropped == 0
    assert row.rejection_reasons == []
    assert row.drop_reasons == []
    agent = league_conn.execute(
        text("SELECT llm_model, grounding_enabled FROM league.agents WHERE id = :agent"),
        ids,
    ).one()
    assert agent.llm_model == "unconfigured"
    assert agent.grounding_enabled is True
    grounding = league_conn.execute(
        text("SELECT grounding FROM league.rationales WHERE system = false"),
    ).scalar_one()
    assert grounding is True
    interests = league_conn.execute(
        text("SELECT interests FROM league.agents WHERE id = :agent"),
        ids,
    ).scalar_one()
    assert interests == []
