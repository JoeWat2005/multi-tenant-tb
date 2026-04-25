"""Tests for position_service helpers (open / close / mark)."""
from __future__ import annotations

from app.services.tenant import position_service


def test_open_position_writes_row(db, tenant, running_session):
    pos = position_service.open_position(
        db,
        tenant_id=tenant.id, session_id=running_session.id,
        symbol="BTC/USD", entry_price=100.0, quantity=2.0, size_pct=10.0,
        stop_loss_price=98.0, take_profit_price=104.0,
    )
    assert pos.id is not None
    assert pos.position_value == 200.0
    assert pos.status == "open"


def test_close_position_winner(db, tenant, running_session):
    pos = position_service.open_position(
        db,
        tenant_id=tenant.id, session_id=running_session.id,
        symbol="BTC/USD", entry_price=100.0, quantity=1.0, size_pct=10.0,
        stop_loss_price=98.0, take_profit_price=104.0,
    )
    pnl = position_service.close_position(
        db, pos, exit_price=110.0, reason="TAKE_PROFIT", exit_fee=1.0,
    )
    # Gross: (110-100)*1 = 10. Net of 1.0 exit fee → 9.0.
    assert pnl == 9.0
    assert pos.status == "closed"
    assert pos.exit_reason == "TAKE_PROFIT"
    assert pos.realised_pnl == 9.0
    assert pos.unrealised_pnl == 0.0


def test_close_position_loser(db, tenant, running_session):
    pos = position_service.open_position(
        db,
        tenant_id=tenant.id, session_id=running_session.id,
        symbol="BTC/USD", entry_price=100.0, quantity=2.0, size_pct=10.0,
        stop_loss_price=98.0, take_profit_price=104.0,
    )
    pnl = position_service.close_position(
        db, pos, exit_price=95.0, reason="STOP_LOSS", exit_fee=0.5,
    )
    # (95-100)*2 = -10, minus 0.5 fee = -10.5
    assert pnl == -10.5
    assert pos.status == "closed"


def test_update_unrealised_only_when_open(db, tenant, running_session):
    pos = position_service.open_position(
        db,
        tenant_id=tenant.id, session_id=running_session.id,
        symbol="BTC/USD", entry_price=100.0, quantity=2.0, size_pct=10.0,
        stop_loss_price=None, take_profit_price=None,
    )
    position_service.update_unrealised(db, pos, current_price=105.0)
    assert pos.unrealised_pnl == 10.0  # (105-100)*2

    pos.status = "closed"
    position_service.update_unrealised(db, pos, current_price=200.0)
    # No-op once closed.
    assert pos.unrealised_pnl == 10.0
