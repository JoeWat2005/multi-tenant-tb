"""Tests for TenantRegistry — idempotent ensure, symbol union."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.orchestration.tenant_registry import TenantRegistry


def _registry():
    return TenantRegistry(decision_provider=MagicMock(), order_queue=MagicMock())


def test_ensure_creates_once_and_returns_same_object(bot_config):
    reg = _registry()
    a = reg.ensure(1, bot_config)
    b = reg.ensure(1, bot_config)
    assert a is b


def test_ensure_updates_tracked_symbols_on_repeat(bot_config):
    reg = _registry()
    a = reg.ensure(1, bot_config)
    assert "BTC/USD" in a.tracked_symbols

    bot_config.symbol = "ETH/USD"
    b = reg.ensure(1, bot_config)
    assert b is a
    assert b.tracked_symbols == {"ETH/USD"}


def test_union_combines_all_tenants(bot_config):
    reg = _registry()
    reg.ensure(1, bot_config)

    other = type(bot_config)(
        tenant_id=2, symbol="ETH/USD", interval="30m", mode="PAPER",
    )
    reg.ensure(2, other)
    assert reg.union_of_tracked_symbols() == {"BTC/USD", "ETH/USD"}


def test_unregister_removes_tenant(bot_config):
    reg = _registry()
    reg.ensure(1, bot_config)
    reg.unregister(1)
    assert reg.get(1) is None
    assert reg.union_of_tracked_symbols() == set()


def test_get_returns_none_for_unknown():
    reg = _registry()
    assert reg.get(999) is None
