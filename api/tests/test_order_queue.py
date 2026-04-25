"""Tests for OrderQueue — type validation + FIFO behaviour."""
from __future__ import annotations

import asyncio

import pytest

from app.services.orchestration.order_queue import (
    EXIT_POSITION_MARKET,
    PLACE_MARKET_BUY,
    OrderQueue,
)


async def test_put_then_get_roundtrip():
    q = OrderQueue()
    await q.put(tenant_id=1, session_id=2, type=PLACE_MARKET_BUY, payload={"x": 1})
    job = await q.get()
    assert job.tenant_id == 1 and job.session_id == 2
    assert job.type == PLACE_MARKET_BUY
    assert job.payload == {"x": 1}
    q.task_done()


async def test_unknown_type_raises():
    q = OrderQueue()
    with pytest.raises(ValueError):
        await q.put(tenant_id=1, session_id=1, type="MAKE_COFFEE")


async def test_default_payload_is_empty_dict():
    q = OrderQueue()
    await q.put(tenant_id=1, session_id=1, type=EXIT_POSITION_MARKET)
    job = await q.get()
    assert job.payload == {}


async def test_qsize_tracks_pending():
    q = OrderQueue()
    assert q.qsize() == 0
    await q.put(tenant_id=1, session_id=1, type=PLACE_MARKET_BUY)
    await q.put(tenant_id=1, session_id=1, type=EXIT_POSITION_MARKET)
    assert q.qsize() == 2
    await q.get()
    assert q.qsize() == 1


async def test_put_threadsafe_works_from_other_thread():
    q = OrderQueue()
    loop = asyncio.get_running_loop()

    def _enqueue():
        q.put_threadsafe(loop, tenant_id=42, session_id=7, type=PLACE_MARKET_BUY)

    await asyncio.to_thread(_enqueue)
    job = await asyncio.wait_for(q.get(), timeout=1.0)
    assert job.tenant_id == 42
