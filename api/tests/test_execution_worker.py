"""Tests for ExecutionWorker handlers — mock Alpaca, exercise the BUY/SELL
DB writes + balance accounting that PAPER mode used to do synthetically."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models.order import Order
from app.models.position import Position
from app.models.trade import Trade
from app.services.orchestration.execution_worker import (
    ExecutionWorker,
    _fill_fee,
    _fill_price,
    _fill_qty,
)
from app.services.orchestration.order_queue import (
    EXIT_POSITION_MARKET,
    PLACE_MARKET_BUY,
    PLACE_OCO_SELL,
    OrderJob,
    OrderQueue,
)


# ── Pure helpers ──────────────────────────────────────────────────────────


def test_fill_price_falls_back_when_missing():
    assert _fill_price(None, fallback=99.0) == 99.0


def test_fill_price_uses_alpaca_value():
    order = MagicMock()
    order.filled_avg_price = "100.5"
    assert _fill_price(order, fallback=0) == 100.5


def test_fill_qty_falls_back_when_missing():
    assert _fill_qty(None, fallback=1.5) == 1.5


def test_fill_qty_prefers_filled_then_qty():
    order = MagicMock(spec=["filled_qty", "qty"])
    order.filled_qty = "0.5"
    order.qty = "9.0"
    assert _fill_qty(order, fallback=0) == 0.5


def test_fill_fee_is_zero_when_unknown():
    order = MagicMock()
    order.commission = None
    assert _fill_fee(order) == 0.0


# ── Handler integration tests ─────────────────────────────────────────────


def _alpaca_buy(price=100.0, qty=0.1, fee=0.05):
    o = MagicMock()
    o.id = "alpaca-id-1"
    o.filled_avg_price = str(price)
    o.filled_qty = str(qty)
    o.commission = str(fee)
    return o


def _alpaca_sell(price=110.0, qty=0.1, fee=0.06):
    return _alpaca_buy(price=price, qty=qty, fee=fee)


def test_place_market_buy_records_trade_position_and_balance(
    db, tenant, running_session, bot_config, monkeypatch, session_factory,
):
    monkeypatch.setattr(
        "app.services.orchestration.execution_worker.SessionLocal", session_factory,
    )
    # Force market price even when WS not connected.
    monkeypatch.setattr(
        ExecutionWorker, "_current_price", staticmethod(lambda symbol: 100.0),
    )
    monkeypatch.setattr(
        ExecutionWorker, "_submit_market_order",
        staticmethod(lambda **kwargs: _alpaca_buy()),
    )

    queue = OrderQueue()
    worker = ExecutionWorker(queue)
    worker._loop = MagicMock()  # for put_threadsafe — we won't actually run it

    job = OrderJob(
        tenant_id=tenant.id, session_id=running_session.id,
        type=PLACE_MARKET_BUY,
        payload={"size_pct": 10.0, "sl_pct": 2.0, "tp_pct": 4.0, "decision_id": 1},
    )
    worker._place_market_buy(job)

    # Trade row written
    trade = db.query(Trade).first()
    assert trade is not None
    assert trade.side == "BUY"
    assert trade.alpaca_order_id == "alpaca-id-1"

    # Position opened
    pos = db.query(Position).first()
    assert pos is not None
    assert pos.status == "open"
    assert trade.position_id == pos.id

    # Balance debited
    db.refresh(running_session)
    assert running_session.current_balance < 10_000.0


def test_place_market_buy_skips_when_session_not_running(
    db, tenant, running_session, monkeypatch, session_factory,
):
    from app.services.tenant import session_service
    running_session.status = session_service.STOPPING
    db.commit()

    monkeypatch.setattr(
        "app.services.orchestration.execution_worker.SessionLocal", session_factory,
    )

    submit = MagicMock()
    monkeypatch.setattr(ExecutionWorker, "_submit_market_order", staticmethod(submit))

    worker = ExecutionWorker(OrderQueue())
    job = OrderJob(
        tenant_id=tenant.id, session_id=running_session.id,
        type=PLACE_MARKET_BUY,
        payload={"size_pct": 10.0, "sl_pct": 2.0, "tp_pct": 4.0, "decision_id": 1},
    )
    worker._place_market_buy(job)

    assert submit.call_count == 0  # didn't even try Alpaca


def test_place_oco_sell_writes_two_legs(db, tenant, running_session, monkeypatch, session_factory):
    pos = Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100.0, quantity=1.0,
        position_value=100.0, size_pct=10.0,
    )
    db.add(pos); db.commit()

    monkeypatch.setattr(
        "app.services.orchestration.execution_worker.SessionLocal", session_factory,
    )

    worker = ExecutionWorker(OrderQueue())
    job = OrderJob(
        tenant_id=tenant.id, session_id=running_session.id,
        type=PLACE_OCO_SELL,
        payload={"position_id": pos.id, "sl_price": 98.0, "tp_price": 104.0},
    )
    worker._place_oco_sell(job)

    legs = db.query(Order).filter(Order.position_id == pos.id).all()
    assert len(legs) == 2
    types = {leg.type for leg in legs}
    assert types == {"STOP_LOSS", "TAKE_PROFIT"}
    # Same OCO group for both
    assert len({leg.oco_group_id for leg in legs}) == 1


def test_exit_position_market_closes_position(db, tenant, running_session, monkeypatch, session_factory):
    pos = Position(
        tenant_id=tenant.id, session_id=running_session.id, symbol="BTC/USD",
        status="open", entry_price=100.0, quantity=0.1,
        position_value=10.0, size_pct=10.0,
        stop_loss_price=98.0, take_profit_price=104.0,
    )
    db.add(pos); db.flush()
    db.add(Order(
        tenant_id=tenant.id, session_id=running_session.id, position_id=pos.id,
        symbol="BTC/USD", side="SELL", type="STOP_LOSS",
        status="active", quantity=0.1, stop_price=98.0, oco_group_id="x",
    ))
    db.commit()

    monkeypatch.setattr(
        "app.services.orchestration.execution_worker.SessionLocal", session_factory,
    )
    monkeypatch.setattr(
        ExecutionWorker, "_current_price", staticmethod(lambda s: 110.0),
    )
    monkeypatch.setattr(
        ExecutionWorker, "_submit_market_order",
        staticmethod(lambda **kwargs: _alpaca_sell()),
    )

    worker = ExecutionWorker(OrderQueue())
    job = OrderJob(
        tenant_id=tenant.id, session_id=running_session.id,
        type=EXIT_POSITION_MARKET,
        payload={"position_id": pos.id, "reason": "AI_SELL"},
    )
    worker._exit_position_market(job)

    db.refresh(pos)
    assert pos.status == "closed"
    # Active OCO leg cancelled.
    legs = db.query(Order).filter(Order.position_id == pos.id).all()
    assert all(leg.status == "cancelled" for leg in legs)
    # Balance credited (it was 10_000, and we sold 0.1 BTC @ 110 = 11)
    db.refresh(running_session)
    assert running_session.current_balance > 10_000


def test_cancel_open_buy_orders_marks_pending(db, tenant, running_session, monkeypatch, session_factory):
    db.add(Order(
        tenant_id=tenant.id, session_id=running_session.id,
        symbol="BTC/USD", side="BUY", type="MARKET",
        status="pending", quantity=1.0,
    ))
    db.commit()
    monkeypatch.setattr(
        "app.services.orchestration.execution_worker.SessionLocal", session_factory,
    )

    worker = ExecutionWorker(OrderQueue())
    job = OrderJob(
        tenant_id=tenant.id, session_id=running_session.id,
        type="CANCEL_OPEN_BUY_ORDERS", payload={},
    )
    worker._cancel_open_buy_orders(job)

    rows = db.query(Order).all()
    assert all(r.status == "cancelled" for r in rows)
