"""Tests for RiskService.evaluate — every gate matters because this is the
last barrier before a real order goes out."""
from __future__ import annotations

import pytest

from app.models.position import Position
from app.services.tenant import session_service
from app.services.tenant.risk_service import RiskService


def _decision(action="BUY", confidence=0.8, sl=2.0, tp=4.0):
    return {
        "action": action,
        "confidence": confidence,
        "stop_loss_pct": sl,
        "take_profit_pct": tp,
    }


def test_kill_switch_forces_hold(monkeypatch, running_session, bot_config, tenant):
    monkeypatch.setenv("KILL_SWITCH", "true")
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision(),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert not check.approved
    assert check.action == "HOLD"
    assert "Kill switch" in check.reason


def test_session_not_running_blocks_action(running_session, bot_config, tenant):
    running_session.status = session_service.STOPPING
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision(),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert not check.approved
    assert "Session not RUNNING" in check.reason


def test_buy_with_open_position_rejected(running_session, bot_config, tenant):
    pos = Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100.0, quantity=1.0,
        position_value=100.0, size_pct=10.0,
    )
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("BUY"),
        session=running_session, config=bot_config,
        open_position=pos, current_price=100.0, balance=10_000.0,
    )
    assert not check.approved
    assert "Already holding" in check.reason


def test_sell_without_open_position_rejected(running_session, bot_config, tenant):
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("SELL"),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert not check.approved
    assert "No open position" in check.reason


def test_low_confidence_buy_rejected(running_session, bot_config, tenant):
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("BUY", confidence=0.4),  # below 0.65 default
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert not check.approved
    assert "Confidence" in check.reason


def test_session_loss_cap_blocks_new_buy(running_session, bot_config, tenant):
    running_session.session_pnl_pct = -5.0  # past 3% cap
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("BUY"),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert not check.approved
    assert "Session loss" in check.reason


def test_session_loss_cap_does_not_block_sell(running_session, bot_config, tenant):
    """Hitting the loss cap should still let us flatten a position."""
    running_session.session_pnl_pct = -5.0
    pos = Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100.0, quantity=1.0,
        position_value=100.0, size_pct=10.0,
    )
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("SELL"),
        session=running_session, config=bot_config,
        open_position=pos, current_price=100.0, balance=10_000.0,
    )
    assert check.approved
    assert check.action == "SELL"


def test_sl_tp_clamped_to_config_caps(running_session, bot_config, tenant):
    """AI suggests an absurd 50% stop-loss → clamped to 2× config (4%)."""
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision(sl=50.0, tp=200.0),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert check.approved
    assert check.stop_loss_pct == bot_config.stop_loss_pct * 2.0   # 4.0
    assert check.take_profit_pct == bot_config.take_profit_pct * 3.0  # 12.0


def test_balance_too_low_for_position_size(running_session, bot_config, tenant):
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("BUY"),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=0.0,
    )
    assert not check.approved
    assert "Balance" in check.reason


def test_hold_passes_through(running_session, bot_config, tenant):
    rs = RiskService(tenant.id)
    check = rs.evaluate(
        _decision("HOLD"),
        session=running_session, config=bot_config,
        open_position=None, current_price=100.0, balance=10_000.0,
    )
    assert check.approved is True
    assert check.action == "HOLD"
