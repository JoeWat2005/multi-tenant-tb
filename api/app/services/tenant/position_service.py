"""Position lifecycle — DB writes for open / mark / close.

Stateless functions. The ExecutionWorker calls these after a confirmed fill.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.models.position import Position

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def open_position(
    db: "Session",
    *,
    tenant_id: int,
    session_id: int,
    symbol: str,
    entry_price: float,
    quantity: float,
    size_pct: float,
    stop_loss_price: float | None,
    take_profit_price: float | None,
) -> Position:
    pos = Position(
        tenant_id=tenant_id,
        session_id=session_id,
        symbol=symbol.upper(),
        status="open",
        entry_price=entry_price,
        quantity=quantity,
        position_value=entry_price * quantity,
        size_pct=size_pct,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        opened_at=datetime.now(timezone.utc),
    )
    db.add(pos)
    db.flush()
    logger.info("Opened position %s %s qty=%.6f @ %.4f (session=%s)",
                pos.id, pos.symbol, quantity, entry_price, session_id)
    return pos


def update_unrealised(db: "Session", position: Position, current_price: float) -> None:
    if position.status != "open" or position.entry_price <= 0:
        return
    position.unrealised_pnl = (current_price - position.entry_price) * position.quantity


def close_position(
    db: "Session",
    position: Position,
    *,
    exit_price: float,
    reason: str,
    exit_fee: float = 0.0,
) -> float:
    """Finalise a position. Returns realised P&L in USD (net of fees)."""
    gross = (exit_price - position.entry_price) * position.quantity
    # Entry fee is already deducted from balance at fill; subtract the exit fee here.
    realised = gross - exit_fee
    position.status = "closed"
    position.exit_price = exit_price
    position.realised_pnl = realised
    position.unrealised_pnl = 0.0
    position.exit_reason = reason
    position.closed_at = datetime.now(timezone.utc)
    db.flush()
    logger.info("Closed position %s exit=%.4f reason=%s pnl=%.2f",
                position.id, exit_price, reason, realised)
    return realised
