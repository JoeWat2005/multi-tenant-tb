"""Open positions — reads the Position table scoped to current session."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.position import Position
from app.models.tenant import Tenant
from app.services.tenant import session_service
from app.util.dates import iso_utc

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("")
def list_positions(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    session = session_service.get_active_session(db, tenant.id)
    if session is None:
        return []
    rows = (
        db.query(Position)
        .filter(Position.session_id == session.id, Position.status == "open")
        .order_by(desc(Position.opened_at))
        .all()
    )
    return [
        {
            "id": p.id,
            "symbol": p.symbol,
            "entry_price": p.entry_price,
            "quantity": p.quantity,
            "position_value": p.position_value,
            "size_pct": p.size_pct,
            "stop_loss_price": p.stop_loss_price,
            "take_profit_price": p.take_profit_price,
            "unrealised_pnl": p.unrealised_pnl,
            "opened_at": iso_utc(p.opened_at),
        }
        for p in rows
    ]
