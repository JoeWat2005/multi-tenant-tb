"""Per-tenant decision cycle — called by tick_scheduler when the tenant is due.

Pipeline:

  1. Confirm there's an active RUNNING session.
  2. Refresh closed candles from the broker.
  3. Build a MarketContext (sentiment, news, technicals, position, memory).
  4. Ask the DecisionProvider (Claude) for a strict-JSON verdict.
  5. Validate via RiskService; record a BotDecision row either way.
  6. Link the persisted MarketContextSnapshot to that BotDecision.
  7. If approved + actionable, enqueue an OrderQueue job.

Nothing here touches paper balance, positions, or orders directly — the
ExecutionWorker owns all fills. Decisions stay reproducible: a frozen
MarketContextSnapshot + BotDecision lets us replay any cycle.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.models.bot_decision import BotDecision
from app.services.orchestration.order_queue import (
    EXIT_POSITION_MARKET,
    PLACE_MARKET_BUY,
)
from app.services.shared import market_data_service, sentiment_service
from app.services.shared.market_context_service import MarketContextService
from app.services.tenant import session_service
from app.services.tenant.risk_service import RiskService

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.bot_config import BotConfig
    from app.services.orchestration.order_queue import OrderQueue
    from app.services.shared.decision_provider import DecisionProvider


class TradingCycleService:
    def __init__(
        self,
        tenant_id: int,
        decision_provider: "DecisionProvider",
        order_queue: "OrderQueue",
    ) -> None:
        self.tenant_id = tenant_id
        self.decision_provider = decision_provider
        self.order_queue = order_queue
        self.risk = RiskService(tenant_id)

    async def run(self, db: "Session", config: "BotConfig") -> None:
        session = session_service.get_active_session(db, self.tenant_id)
        if session is None or session.status != session_service.RUNNING:
            logger.info("Cycle skip tenant=%s — no RUNNING session", self.tenant_id)
            return

        symbol = (config.symbol or "BTC/USD").upper()
        interval = config.interval or "30m"
        logger.info("Cycle tenant=%s session=%s symbol=%s interval=%s",
                    self.tenant_id, session.id, symbol, interval)

        # 1. Refresh closed candles from the broker.
        await asyncio.to_thread(market_data_service.refresh, db, symbol, interval)

        # 2. Build the full context (writes a MarketContextSnapshot row).
        ctx = await asyncio.to_thread(
            MarketContextService.build, db, session=session, config=config,
        )
        if not ctx.candles:
            logger.warning("Cycle tenant=%s: no candles available, skipping", self.tenant_id)
            return

        # 3. Ask the AI.
        try:
            decision = await self.decision_provider.decide(
                ctx,
                min_confidence=float(config.min_confidence),
                max_position_pct=float(config.position_size_pct),
            )
        except Exception:
            logger.exception("Decision provider failed for tenant %s", self.tenant_id)
            return

        # 4. Risk gate.
        last_price = ctx.candles[-1]["close"] if ctx.candles else 0.0
        open_pos = (
            session_service.get_open_position(db, session.id)
            if ctx.open_position is not None
            else None
        )
        check = self.risk.evaluate(
            {
                "action": decision.action,
                "confidence": decision.confidence,
                "stop_loss_pct": decision.stop_loss_pct,
                "take_profit_pct": decision.take_profit_pct,
            },
            session=session,
            config=config,
            open_position=open_pos,
            current_price=last_price,
            balance=session.current_balance,
        )

        # 5. Persist the BotDecision (approved or not).
        bd = BotDecision(
            tenant_id=self.tenant_id,
            session_id=session.id,
            symbol=symbol,
            action=check.action,
            confidence=float(decision.confidence),
            reason=decision.reasoning,
            sentiment_score=sentiment_service.latest_raw_score(db, symbol)
            if config.use_sentiment else None,
            weighted_sentiment_score=ctx.weighted_sentiment_score,
            price_snapshot=last_price,
            approved=check.approved,
            rejection_reason=None if check.approved else check.reason,
            created_at=datetime.now(timezone.utc),
        )
        db.add(bd)
        db.commit()

        # 6. Link the snapshot back to this decision.
        await asyncio.to_thread(
            MarketContextService.attach_decision_id, db, ctx, bd.id,
        )

        # 7. Enqueue order if approved + actionable.
        if not check.approved or check.action == "HOLD":
            return

        if check.action == "BUY":
            await self.order_queue.put(
                tenant_id=self.tenant_id,
                session_id=session.id,
                type=PLACE_MARKET_BUY,
                payload={
                    "size_pct": check.size_pct,
                    "sl_pct": check.stop_loss_pct,
                    "tp_pct": check.take_profit_pct,
                    "decision_id": bd.id,
                },
            )
        elif check.action == "SELL" and open_pos is not None:
            await self.order_queue.put(
                tenant_id=self.tenant_id,
                session_id=session.id,
                type=EXIT_POSITION_MARKET,
                payload={"position_id": open_pos.id, "reason": "AI_SELL"},
            )
