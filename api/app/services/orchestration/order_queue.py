"""Async order queue. All order placement and cancellation goes through this.

Separating the decision loop from the execution loop is what keeps the
system safe: if the queue backs up, decisions still record cleanly as
BotDecision rows; nothing gets "double-fired" because each job is handled
once by one worker.

Job types (matches the MVP plan):
  PLACE_MARKET_BUY          — open a new position at market
  PLACE_OCO_SELL            — attach SL/TP to an open position
  CANCEL_OPEN_BUY_ORDERS    — cancel any unfilled BUY orders for a session
  EXIT_POSITION_MARKET      — close a position immediately at market
  SYNC_ORDER_STATUS         — reconcile an order against the exchange

`payload` is a plain dict — each handler knows its shape.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Job type constants (import these instead of typing strings).
PLACE_MARKET_BUY = "PLACE_MARKET_BUY"
PLACE_OCO_SELL = "PLACE_OCO_SELL"
CANCEL_OPEN_BUY_ORDERS = "CANCEL_OPEN_BUY_ORDERS"
EXIT_POSITION_MARKET = "EXIT_POSITION_MARKET"
SYNC_ORDER_STATUS = "SYNC_ORDER_STATUS"

JOB_TYPES = {
    PLACE_MARKET_BUY, PLACE_OCO_SELL, CANCEL_OPEN_BUY_ORDERS,
    EXIT_POSITION_MARKET, SYNC_ORDER_STATUS,
}


@dataclass
class OrderJob:
    tenant_id: int
    session_id: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


class OrderQueue:
    """Thin wrapper over asyncio.Queue so handler threads can enqueue jobs
    via `put_threadsafe` without touching a non-thread-safe queue directly."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._q: asyncio.Queue[OrderJob] = asyncio.Queue(maxsize=maxsize)

    @staticmethod
    def _build(tenant_id: int, session_id: int, type: str,
               payload: dict[str, Any] | None) -> OrderJob:
        if type not in JOB_TYPES:
            raise ValueError(f"Unknown order job type: {type}")
        return OrderJob(tenant_id=tenant_id, session_id=session_id,
                        type=type, payload=payload or {})

    async def put(self, *, tenant_id: int, session_id: int, type: str,
                  payload: dict[str, Any] | None = None) -> None:
        job = self._build(tenant_id, session_id, type, payload)
        await self._q.put(job)
        logger.debug("Enqueued %s tenant=%s session=%s", type, tenant_id, session_id)

    def put_threadsafe(self, loop: asyncio.AbstractEventLoop, *,
                       tenant_id: int, session_id: int, type: str,
                       payload: dict[str, Any] | None = None) -> None:
        """Enqueue from a worker thread. Non-blocking on the caller's side."""
        job = self._build(tenant_id, session_id, type, payload)
        loop.call_soon_threadsafe(self._q.put_nowait, job)
        logger.debug("Enqueued (ts) %s tenant=%s session=%s",
                     type, tenant_id, session_id)

    async def get(self) -> OrderJob:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()
