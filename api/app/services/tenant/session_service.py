"""Session state machine — owns the lifecycle transitions.

Statuses and allowed transitions:

    IDLE      -> STARTING                  (start_session)
    STARTING  -> RUNNING                   (finish_start)
    RUNNING   -> STOPPING                  (stop_session)
    STOPPING  -> EXITING                   (if open position) or ENDED
    EXITING   -> ENDED                     (after exit fill)
    *         -> ERROR                     (any unrecoverable failure)

Nothing in this module places orders directly — it enqueues jobs on the shared
OrderQueue. The ExecutionWorker drives fills and calls `finalise_if_done` after
every state-changing fill to decide if the session is ready to close.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.models.bot_config import BotConfig
from app.models.equity_snapshot import EquitySnapshot
from app.models.order import Order
from app.models.position import Position
from app.models.trading_session import TradingSession

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.services.orchestration.order_queue import OrderQueue


# ── Statuses ──────────────────────────────────────────────────────────────

IDLE = "IDLE"
STARTING = "STARTING"
RUNNING = "RUNNING"
STOPPING = "STOPPING"
EXITING = "EXITING"
ENDED = "ENDED"
ERROR = "ERROR"

TERMINAL = {ENDED, ERROR}
ACTIVE = {STARTING, RUNNING, STOPPING, EXITING}


# ── Queries ───────────────────────────────────────────────────────────────

def get_active_session(db: "Session", tenant_id: int) -> TradingSession | None:
    """The one non-terminal session for a tenant, or None."""
    return (
        db.query(TradingSession)
        .filter(
            TradingSession.tenant_id == tenant_id,
            TradingSession.status.in_(list(ACTIVE)),
        )
        .order_by(TradingSession.id.desc())
        .first()
    )


def get_open_position(db: "Session", session_id: int) -> Position | None:
    return (
        db.query(Position)
        .filter(Position.session_id == session_id, Position.status == "open")
        .order_by(Position.id.desc())
        .first()
    )


def count_pending_orders(db: "Session", session_id: int) -> int:
    return (
        db.query(Order)
        .filter(
            Order.session_id == session_id,
            Order.status.in_(["pending", "submitted", "active"]),
        )
        .count()
    )


# ── Transitions ───────────────────────────────────────────────────────────

def start_session(db: "Session", tenant_id: int, config: BotConfig) -> TradingSession:
    """Create a new session, flip config.running=True, schedule first cycle.

    Idempotent: if an active session already exists, returns it unchanged.
    """
    existing = get_active_session(db, tenant_id)
    if existing is not None:
        logger.info("Tenant %s already has active session %s (%s)",
                    tenant_id, existing.id, existing.status)
        return existing

    now = datetime.now(timezone.utc)
    session = TradingSession(
        tenant_id=tenant_id,
        config_id=config.id,
        symbol=config.symbol,
        interval=config.interval,
        mode=config.mode,
        status=STARTING,
        starting_balance=config.paper_balance_usd,
        current_balance=config.paper_balance_usd,
        started_at=now,
    )
    db.add(session)
    db.flush()

    db.add(EquitySnapshot(
        session_id=session.id,
        balance=session.current_balance,
        unrealised_pnl=0.0,
        realised_pnl=0.0,
        total_equity=session.current_balance,
        created_at=now,
    ))

    config.running = True
    config.next_run_at = now  # tick scheduler fires on next tick

    session.status = RUNNING
    db.commit()
    db.refresh(session)
    logger.info("Tenant %s session %s started (%s %s)",
                tenant_id, session.id, session.symbol, session.mode)
    return session


async def stop_session(
    db: "Session",
    tenant_id: int,
    order_queue: "OrderQueue",
) -> TradingSession | None:
    """User clicked Stop. Enter STOPPING, cancel pending buys, start exit if
    a position is open. No new BUY decisions will be issued while STOPPING or
    EXITING (RiskService enforces this).

    Transitions directly to ENDED if nothing is open.
    """
    session = get_active_session(db, tenant_id)
    if session is None:
        return None

    cfg = db.query(BotConfig).filter(BotConfig.tenant_id == tenant_id).first()
    if cfg is not None:
        cfg.running = False
        cfg.next_run_at = None

    now = datetime.now(timezone.utc)
    session.stop_requested_at = now
    session.status = STOPPING
    db.commit()

    # 1. Cancel any pending BUY orders (idempotent in the worker).
    await order_queue.put(
        tenant_id=tenant_id,
        session_id=session.id,
        type="CANCEL_OPEN_BUY_ORDERS",
        payload={},
    )

    # 2. If a position is open, cancel its OCO exits and market-SELL.
    open_pos = get_open_position(db, session.id)
    if open_pos is not None:
        session.status = EXITING
        db.commit()
        await order_queue.put(
            tenant_id=tenant_id,
            session_id=session.id,
            type="EXIT_POSITION_MARKET",
            payload={"position_id": open_pos.id, "reason": "USER_STOP"},
        )
    else:
        end_session(db, session, reason="No open position at stop")

    return session


def end_session(db: "Session", session: TradingSession, reason: str = "") -> None:
    """Mark session ENDED, compute final P&L, snapshot ending equity."""
    if session.status == ENDED:
        return

    now = datetime.now(timezone.utc)
    session.status = ENDED
    session.ended_at = now
    session.ending_balance = session.current_balance

    pnl = session.current_balance - session.starting_balance
    session.session_pnl = pnl
    session.session_pnl_pct = (
        (pnl / session.starting_balance * 100) if session.starting_balance > 0 else 0.0
    )

    db.add(EquitySnapshot(
        session_id=session.id,
        balance=session.current_balance,
        unrealised_pnl=0.0,
        realised_pnl=pnl,
        total_equity=session.current_balance,
        created_at=now,
    ))
    db.commit()
    logger.info("Session %s ended (P&L %.2f, reason=%s)", session.id, pnl, reason)


def mark_error(db: "Session", session: TradingSession, message: str) -> None:
    session.status = ERROR
    session.error_message = message
    session.ended_at = datetime.now(timezone.utc)
    session.ending_balance = session.current_balance
    db.commit()
    logger.error("Session %s marked ERROR: %s", session.id, message)


def finalise_if_done(db: "Session", session_id: int) -> None:
    """Called by ExecutionWorker after every fill. If we're in STOPPING/EXITING
    and there are no open positions and no pending orders, end the session."""
    session = db.query(TradingSession).filter(TradingSession.id == session_id).first()
    if session is None or session.status not in (STOPPING, EXITING):
        return

    if get_open_position(db, session.id) is not None:
        return
    if count_pending_orders(db, session.id) > 0:
        return

    end_session(db, session, reason="all positions closed")
