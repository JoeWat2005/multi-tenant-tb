"""Client-side OCO trigger.

Alpaca does NOT support server-side bracket / OCO orders for crypto
(only equities), so this service is the trigger for both PAPER and
LIVE modes. Every `TICK_SECONDS` we scan active STOP_LOSS / TAKE_PROFIT
orders, read the latest price from MarketStateStore, and enqueue
EXIT_POSITION_MARKET if a leg is in the money. ExecutionWorker then
submits the real market SELL to Alpaca.

Atomicity: we flip the order status from `active` to `triggered` in
a single UPDATE and check the rowcount — the losing racer just skips.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.db.session import SessionLocal
from app.models.order import Order
from app.models.trading_session import TradingSession
from app.services.orchestration.order_queue import EXIT_POSITION_MARKET
from app.services.tenant import session_service

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.orchestration.order_queue import OrderQueue
    from app.services.shared.market_state import MarketStateStore


TICK_SECONDS = 2


class OcoTriggerService:
    def __init__(
        self,
        market_store: "MarketStateStore",
        order_queue: "OrderQueue",
    ) -> None:
        self.market_store = market_store
        self.order_queue = order_queue
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="oco-trigger")
        logger.info("OcoTriggerService started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._tick)
            except Exception:
                logger.exception("OcoTriggerService tick failed")
            try:
                await asyncio.sleep(TICK_SECONDS)
            except asyncio.CancelledError:
                raise

    def _tick(self) -> None:
        db = SessionLocal()
        try:
            # Alpaca crypto doesn't support server-side OCO, so we trigger
            # client-side for every active session regardless of mode.
            rows = (
                db.query(Order)
                .join(TradingSession, TradingSession.id == Order.session_id)
                .filter(
                    Order.status == "active",
                    Order.type.in_(["STOP_LOSS", "TAKE_PROFIT"]),
                    TradingSession.status.in_(
                        [session_service.RUNNING, session_service.STOPPING]
                    ),
                )
                .all()
            )
            for order in rows:
                price = self.market_store.last_price(order.symbol)
                if price is None or price <= 0:
                    continue
                triggered = False
                if order.type == "STOP_LOSS" and order.stop_price is not None:
                    if price <= float(order.stop_price):
                        triggered = True
                elif order.type == "TAKE_PROFIT" and order.price is not None:
                    if price >= float(order.price):
                        triggered = True

                if not triggered:
                    continue

                # Atomic claim: only one caller can flip active -> triggered.
                affected = (
                    db.query(Order)
                    .filter(Order.id == order.id, Order.status == "active")
                    .update({"status": "triggered"})
                )
                db.commit()
                if not affected:
                    continue

                reason = "STOP_LOSS" if order.type == "STOP_LOSS" else "TAKE_PROFIT"
                logger.info("OCO %s triggered for position %s (price=%.4f)",
                            reason, order.position_id, price)

                if self._loop is not None:
                    self.order_queue.put_threadsafe(
                        self._loop,
                        tenant_id=order.tenant_id,
                        session_id=order.session_id,
                        type=EXIT_POSITION_MARKET,
                        payload={"position_id": order.position_id, "reason": reason},
                    )
        finally:
            db.close()
