"""Tests for OcoTriggerService._tick — atomic claim + correct trigger
direction. We don't run the loop; we just exercise the tick directly."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.models.order import Order
from app.models.position import Position
from app.services.orchestration.oco_trigger import OcoTriggerService
from app.services.orchestration.order_queue import OrderQueue


def _build(db, tenant, running_session, *, sl=98.0, tp=104.0):
    pos = Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100.0, quantity=1.0,
        position_value=100.0, size_pct=10.0,
        stop_loss_price=sl, take_profit_price=tp,
    )
    db.add(pos); db.flush()
    sl_order = Order(
        tenant_id=tenant.id, session_id=running_session.id, position_id=pos.id,
        symbol="BTC/USD", side="SELL", type="STOP_LOSS",
        status="active", quantity=1.0, stop_price=sl, oco_group_id="test",
    )
    tp_order = Order(
        tenant_id=tenant.id, session_id=running_session.id, position_id=pos.id,
        symbol="BTC/USD", side="SELL", type="TAKE_PROFIT",
        status="active", quantity=1.0, price=tp, oco_group_id="test",
    )
    db.add_all([sl_order, tp_order])
    db.commit()
    return pos, sl_order, tp_order


def _service(price: float | None) -> tuple[OcoTriggerService, MagicMock]:
    market = MagicMock()
    market.last_price = MagicMock(return_value=price)
    queue = OrderQueue()
    svc = OcoTriggerService(market, queue)
    # Pretend a loop is running so put_threadsafe doesn't crash.
    svc._loop = MagicMock()
    return svc, queue


def test_stop_loss_fires_when_price_below(db, tenant, running_session, monkeypatch, session_factory):
    pos, sl_order, tp_order = _build(db, tenant, running_session)
    svc, queue = _service(price=97.5)  # below SL of 98

    monkeypatch.setattr(
        "app.services.orchestration.oco_trigger.SessionLocal", session_factory,
    )
    svc._tick()

    db.refresh(sl_order); db.refresh(tp_order)
    assert sl_order.status == "triggered"
    assert tp_order.status == "active"  # untouched


def test_take_profit_fires_when_price_above(db, tenant, running_session, monkeypatch, session_factory):
    pos, sl_order, tp_order = _build(db, tenant, running_session)
    svc, queue = _service(price=105.0)  # above TP of 104

    monkeypatch.setattr(
        "app.services.orchestration.oco_trigger.SessionLocal", session_factory,
    )
    svc._tick()

    db.refresh(sl_order); db.refresh(tp_order)
    assert tp_order.status == "triggered"
    assert sl_order.status == "active"


def test_no_trigger_when_inside_range(db, tenant, running_session, monkeypatch, session_factory):
    pos, sl_order, tp_order = _build(db, tenant, running_session)
    svc, queue = _service(price=101.0)  # between SL and TP

    monkeypatch.setattr(
        "app.services.orchestration.oco_trigger.SessionLocal", session_factory,
    )
    svc._tick()

    db.refresh(sl_order); db.refresh(tp_order)
    assert sl_order.status == "active"
    assert tp_order.status == "active"


def test_skips_when_market_price_unavailable(db, tenant, running_session, monkeypatch, session_factory):
    pos, sl_order, _ = _build(db, tenant, running_session)
    svc, _ = _service(price=None)

    monkeypatch.setattr(
        "app.services.orchestration.oco_trigger.SessionLocal", session_factory,
    )
    svc._tick()

    db.refresh(sl_order)
    assert sl_order.status == "active"
