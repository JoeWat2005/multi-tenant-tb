"""In-process pub/sub for per-tenant events.

Producers: trading_cycle (new AIDecision, new Execution), trigger_executor
           (trade closed), alpaca_ws (live tick — already streamed separately).
Consumers: the per-user WebSocket endpoint subscribes to its own tenant_id
           channel; events get serialised to JSON and pushed to the browser.

Single-process only. If we ever horizontally scale the API, swap this for
Redis pub/sub with the same interface.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        # tenant_id -> set of queues. Each subscriber owns one queue.
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, tenant_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[tenant_id].add(queue)
        return queue

    def unsubscribe(self, tenant_id: int, queue: asyncio.Queue) -> None:
        self._subscribers[tenant_id].discard(queue)

    def publish(self, tenant_id: int, event: dict[str, Any]) -> None:
        """Non-blocking fan-out. Full queues drop the message (log warning)."""
        for queue in self._subscribers.get(tenant_id, ()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("EventBus: subscriber queue full, dropping event for tenant %s", tenant_id)
