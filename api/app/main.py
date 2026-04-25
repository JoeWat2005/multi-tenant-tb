"""FastAPI entry point. Lifespan wires shared-plane services (order queue,
execution worker, tenant registry) and starts the tick scheduler that
drives per-tenant decision cycles.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import state
from app.api.routes.bot_control import router as bot_control_router
from app.api.routes.chart import router as chart_router
from app.api.routes.clerk_webhooks import router as clerk_webhooks_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.health import router as health_router
from app.api.routes.positions import router as positions_router
from app.api.routes.session import router as session_router
from app.api.routes.trades import router as trades_router
from app.api.routes.websocket import router as websocket_router
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.orchestration.execution_worker import ExecutionWorker
from app.services.orchestration.oco_trigger import OcoTriggerService
from app.services.orchestration.order_queue import OrderQueue
from app.services.orchestration.tenant_registry import TenantRegistry
from app.services.orchestration.tick_scheduler import TickScheduler
from app.services.shared.alpaca_ws import AlpacaMarketWS
from app.services.shared.claude_provider import ClaudeDecisionProvider
from app.services.shared.sentiment_service import SentimentRefresher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()

    # Shared-plane singletons
    state.decision_provider = ClaudeDecisionProvider()
    state.order_queue = OrderQueue()
    state.execution_worker = ExecutionWorker(state.order_queue)
    state.tenant_registry = TenantRegistry(
        decision_provider=state.decision_provider,
        order_queue=state.order_queue,
    )
    state.tick_scheduler = TickScheduler(state.tenant_registry)
    state.market_ws = AlpacaMarketWS(state.market_store, state.event_bus)
    state.oco_trigger = OcoTriggerService(state.market_store, state.order_queue)
    state.sentiment_refresher = SentimentRefresher(state.tenant_registry)

    # Restore tenant services for anyone whose session was mid-run on shutdown.
    db = SessionLocal()
    try:
        from app.models.bot_config import BotConfig
        for cfg in db.query(BotConfig).filter(BotConfig.running == True).all():  # noqa: E712
            state.tenant_registry.ensure(cfg.tenant_id, cfg)
    finally:
        db.close()

    # Seed market WS subscription from restored tenants before starting it.
    state.market_ws.set_subscription(state.tenant_registry.union_of_tracked_symbols())

    # Start long-running tasks
    state.execution_worker.start()
    state.market_ws.start()
    state.oco_trigger.start()
    state.sentiment_refresher.start()
    state.scheduler.add_job(
        state.tick_scheduler.tick, "interval",
        seconds=60, id="tick", replace_existing=True,
    )
    state.scheduler.start()

    logger.info("v2 API started")
    yield
    state.scheduler.shutdown(wait=False)
    if state.sentiment_refresher is not None:
        await state.sentiment_refresher.stop()
    if state.oco_trigger is not None:
        await state.oco_trigger.stop()
    if state.market_ws is not None:
        await state.market_ws.stop()
    if state.execution_worker is not None:
        await state.execution_worker.stop()
    logger.info("v2 API stopped")


app = FastAPI(title="Claude Trader v2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(bot_control_router)
app.include_router(session_router)
app.include_router(decisions_router)
app.include_router(trades_router)
app.include_router(positions_router)
app.include_router(chart_router)
app.include_router(websocket_router)
app.include_router(clerk_webhooks_router)
