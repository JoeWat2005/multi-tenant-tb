"""Process-wide singletons. Built lazily in main.py lifespan."""
from __future__ import annotations

from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.event_bus import EventBus
from app.services.shared.market_state import MarketStateStore

if TYPE_CHECKING:
    from app.services.orchestration.execution_worker import ExecutionWorker
    from app.services.orchestration.oco_trigger import OcoTriggerService
    from app.services.orchestration.order_queue import OrderQueue
    from app.services.orchestration.tenant_registry import TenantRegistry
    from app.services.orchestration.tick_scheduler import TickScheduler
    from app.services.shared.alpaca_ws import AlpacaMarketWS
    from app.services.shared.claude_provider import ClaudeDecisionProvider
    from app.services.shared.sentiment_service import SentimentRefresher


# Always-available singletons
market_store = MarketStateStore()
event_bus = EventBus()
scheduler = AsyncIOScheduler(timezone="UTC")

# Built in lifespan
decision_provider: "ClaudeDecisionProvider | None" = None
order_queue: "OrderQueue | None" = None
execution_worker: "ExecutionWorker | None" = None
tenant_registry: "TenantRegistry | None" = None
tick_scheduler: "TickScheduler | None" = None
market_ws: "AlpacaMarketWS | None" = None
oco_trigger: "OcoTriggerService | None" = None
sentiment_refresher: "SentimentRefresher | None" = None
