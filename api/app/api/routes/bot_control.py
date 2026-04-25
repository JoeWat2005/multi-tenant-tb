"""Bot config + session start/stop. All session lifecycle routes live here
so the UI has a single entry point for "the bot"."""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import state
from app.auth.dependencies import current_tenant, get_db
from app.models.bot_config import BotConfig
from app.models.tenant import Tenant
from app.services.tenant import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot", tags=["bot"])


# ── Schemas ───────────────────────────────────────────────────────────────


class BotConfigOut(BaseModel):
    running: bool
    symbol: str
    interval: str
    mode: str
    position_size_pct: float
    min_confidence: float
    stop_loss_pct: float
    take_profit_pct: float
    max_session_loss_pct: float
    sentiment_interval_minutes: int
    decision_lookback_candles: int
    use_sentiment: bool
    use_ai: bool
    paper_balance_usd: float
    model_name: str

    class Config:
        from_attributes = True


ALLOWED_INTERVALS = {"30m", "1h", "4h"}


class BotConfigIn(BaseModel):
    symbol: str | None = None
    interval: str | None = Field(None, pattern="^(30m|1h|4h)$")
    mode: str | None = Field(None, pattern="^(PAPER|LIVE)$")
    position_size_pct: float | None = Field(None, ge=0, le=100)
    min_confidence: float | None = Field(None, ge=0, le=1)
    stop_loss_pct: float | None = Field(None, ge=0, le=100)
    take_profit_pct: float | None = Field(None, ge=0, le=100)
    max_session_loss_pct: float | None = Field(None, ge=0, le=100)
    sentiment_interval_minutes: int | None = Field(None, ge=1, le=1440)
    decision_lookback_candles: int | None = Field(None, ge=10, le=500)
    use_sentiment: bool | None = None
    use_ai: bool | None = None
    paper_balance_usd: float | None = Field(None, ge=0)
    model_name: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _get_or_create_config(db: Session, tenant_id: int) -> BotConfig:
    cfg = db.query(BotConfig).filter(BotConfig.tenant_id == tenant_id).first()
    if cfg is None:
        cfg = BotConfig(tenant_id=tenant_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


# ── Config ────────────────────────────────────────────────────────────────


@router.get("/config", response_model=BotConfigOut)
def get_config(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> BotConfig:
    return _get_or_create_config(db, tenant.id)


@router.put("/config", response_model=BotConfigOut)
def put_config(
    body: BotConfigIn,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> BotConfig:
    cfg = _get_or_create_config(db, tenant.id)
    active = session_service.get_active_session(db, tenant.id)

    patch = body.model_dump(exclude_none=True)

    # Everything except `model_name` is locked while a session is active —
    # the AI picks up `model_name` on the next cycle without restart.
    if active is not None:
        for k in list(patch.keys()):
            if k not in {"model_name"}:
                patch.pop(k)

    if "symbol" in patch and patch["symbol"]:
        patch["symbol"] = patch["symbol"].strip().upper()

    for k, v in patch.items():
        setattr(cfg, k, v)
    db.commit()
    db.refresh(cfg)
    return cfg


# ── Status / Start / Stop ─────────────────────────────────────────────────


@router.get("/status")
def status(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    cfg = _get_or_create_config(db, tenant.id)
    session = session_service.get_active_session(db, tenant.id)
    return {
        "tenant_id": tenant.id,
        "running": cfg.running,
        "mode": cfg.mode,
        "symbol": cfg.symbol,
        "session_status": session.status if session else "IDLE",
        "session_id": session.id if session else None,
        "paper_balance_usd": cfg.paper_balance_usd,
    }


@router.post("/start")
async def start(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if state.tenant_registry is None:
        raise HTTPException(503, "Registry not ready")
    cfg = _get_or_create_config(db, tenant.id)

    # Alpaca keys are required for both PAPER (paper-api.alpaca.markets) and
    # LIVE. The global ALPACA_PAPER=true switch in .env overrides mode=LIVE
    # to paper as a safety net, so a missing key set is the only hard block.
    from app.config import settings as app_settings
    if not app_settings.alpaca_api_key or not app_settings.alpaca_api_secret:
        raise HTTPException(
            400,
            "Alpaca API keys are not configured. Add ALPACA_API_KEY + ALPACA_API_SECRET to your .env.",
        )

    # Prep all the data the first cycle will need — synchronously, before we
    # flip the session to RUNNING + set next_run_at=now. This adds a few
    # seconds of latency to the start request but guarantees the first
    # BotDecision row sees real candles + sentiment instead of NULLs. After
    # this initial seed, the regular cadences (TickScheduler 60s + Sentiment
    # Refresher 15m) take over and operate normally.
    await _seed_initial_data(cfg.symbol, cfg.interval)

    session = session_service.start_session(db, tenant.id, cfg)
    state.tenant_registry.ensure(tenant.id, cfg)

    # Make sure the market WS is subscribed to this tenant's symbol and
    # reconnect so the new sub set takes effect immediately.
    if state.market_ws is not None:
        state.market_ws.set_subscription(state.tenant_registry.union_of_tracked_symbols())
        await state.market_ws.reconnect()

    return {"ok": True, "session_id": session.id, "status": session.status}


async def _seed_initial_data(symbol: str, interval: str) -> None:
    """Run on /start before the session goes RUNNING. In order:
      1. Pull recent closed candles from Alpaca → `candles` table.
         (Required so `price_action` has data to score and so the first
         cycle's `MarketContext.candles` is non-empty.)
      2. Refresh every sentiment source against the freshly-loaded candles.
    Each refresh function swallows its own errors, and the outer try/except
    means a flaky upstream can't block the user from starting the bot."""
    from app.db.session import SessionLocal
    from app.services.shared import market_data_service, sentiment_service

    def _run() -> None:
        db = SessionLocal()
        try:
            try:
                market_data_service.refresh(db, symbol, interval)
            except Exception:
                logger.exception("Initial candle refresh failed for %s", symbol)
            sentiment_service.refresh_fear_greed(db)
            sentiment_service.refresh_alpaca_news(db, symbol)
            sentiment_service.refresh_rss(db, symbol)
            sentiment_service.refresh_price_action(db, symbol)
        finally:
            db.close()

    try:
        # Hard cap so a stuck upstream doesn't keep the user waiting forever.
        await asyncio.wait_for(asyncio.to_thread(_run), timeout=20.0)
        logger.info("Initial seed complete for %s", symbol)
    except asyncio.TimeoutError:
        logger.warning("Initial seed timed out after 20s for %s — starting anyway", symbol)
    except Exception:
        logger.exception("Initial seed failed for %s", symbol)


@router.post("/stop")
async def stop(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if state.order_queue is None:
        raise HTTPException(503, "Queue not ready")
    session = await session_service.stop_session(db, tenant.id, state.order_queue)
    if session is None:
        return {"ok": True, "status": "IDLE"}
    return {"ok": True, "session_id": session.id, "status": session.status}
