"""Bot config + session start/stop. All session lifecycle routes live here
so the UI has a single entry point for "the bot"."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import state
from app.auth.dependencies import current_tenant, get_db
from app.models.bot_config import BotConfig
from app.models.tenant import Tenant
from app.services.tenant import session_service

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

    session = session_service.start_session(db, tenant.id, cfg)
    state.tenant_registry.ensure(tenant.id, cfg)

    # Make sure the market WS is subscribed to this tenant's symbol and
    # reconnect so the new sub set takes effect immediately.
    if state.market_ws is not None:
        state.market_ws.set_subscription(state.tenant_registry.union_of_tracked_symbols())
        await state.market_ws.reconnect()

    return {"ok": True, "session_id": session.id, "status": session.status}


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
