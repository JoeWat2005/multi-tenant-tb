"""Tests for session_service — the state machine that owns
IDLE → STARTING → RUNNING → STOPPING → EXITING → ENDED transitions."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from app.models.bot_config import BotConfig
from app.models.position import Position
from app.models.trading_session import TradingSession
from app.services.tenant import session_service


def test_start_session_creates_running_session(db, tenant, bot_config):
    sess = session_service.start_session(db, tenant.id, bot_config)
    assert sess.status == session_service.RUNNING
    assert sess.starting_balance == bot_config.paper_balance_usd
    assert sess.current_balance == bot_config.paper_balance_usd
    assert bot_config.running is True
    assert bot_config.next_run_at is not None


def test_start_session_is_idempotent(db, tenant, bot_config):
    first = session_service.start_session(db, tenant.id, bot_config)
    second = session_service.start_session(db, tenant.id, bot_config)
    assert first.id == second.id


def test_get_active_session_returns_only_non_terminal(db, tenant, bot_config):
    ended = TradingSession(
        tenant_id=tenant.id, config_id=bot_config.id, symbol="BTC/USD",
        interval="30m", mode="PAPER", status=session_service.ENDED,
        starting_balance=10_000, current_balance=10_500,
    )
    db.add(ended); db.commit()

    assert session_service.get_active_session(db, tenant.id) is None

    running = TradingSession(
        tenant_id=tenant.id, config_id=bot_config.id, symbol="BTC/USD",
        interval="30m", mode="PAPER", status=session_service.RUNNING,
        starting_balance=10_000, current_balance=10_000,
    )
    db.add(running); db.commit()

    assert session_service.get_active_session(db, tenant.id).id == running.id


def test_end_session_computes_pnl(db, tenant, bot_config, running_session):
    running_session.current_balance = 11_000
    db.commit()
    session_service.end_session(db, running_session, reason="test")

    assert running_session.status == session_service.ENDED
    assert running_session.session_pnl == 1_000
    assert running_session.session_pnl_pct == 10.0
    assert running_session.ending_balance == 11_000


def test_end_session_is_idempotent(db, running_session):
    session_service.end_session(db, running_session, reason="test")
    first_ended_at = running_session.ended_at
    session_service.end_session(db, running_session, reason="test again")
    # Second call should be a no-op.
    assert running_session.ended_at == first_ended_at


def test_finalise_if_done_ends_when_no_open_position(db, running_session):
    running_session.status = session_service.STOPPING
    db.commit()
    session_service.finalise_if_done(db, running_session.id)
    assert running_session.status == session_service.ENDED


def test_finalise_if_done_waits_when_position_open(db, running_session, tenant):
    running_session.status = session_service.STOPPING
    db.add(Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100, quantity=1, position_value=100, size_pct=10,
    ))
    db.commit()
    session_service.finalise_if_done(db, running_session.id)
    # Position is still open → must stay STOPPING.
    assert running_session.status == session_service.STOPPING


def test_mark_error_records_message(db, running_session):
    session_service.mark_error(db, running_session, "boom")
    assert running_session.status == session_service.ERROR
    assert running_session.error_message == "boom"


async def test_stop_session_with_no_position_ends_immediately(db, tenant, bot_config, running_session):
    from unittest.mock import AsyncMock
    queue = MagicMock()
    queue.put = AsyncMock()
    sess = await session_service.stop_session(db, tenant.id, queue)
    assert sess.status == session_service.ENDED
    # CANCEL_OPEN_BUY_ORDERS was enqueued.
    assert queue.put.call_count == 1


async def test_stop_session_with_open_position_enters_exiting(db, tenant, bot_config, running_session):
    db.add(Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100, quantity=1, position_value=100, size_pct=10,
    ))
    db.commit()
    from unittest.mock import AsyncMock
    queue = MagicMock()
    queue.put = AsyncMock()
    sess = await session_service.stop_session(db, tenant.id, queue)
    assert sess.status == session_service.EXITING
    # Both CANCEL_OPEN_BUY_ORDERS and EXIT_POSITION_MARKET enqueued.
    assert queue.put.call_count == 2
