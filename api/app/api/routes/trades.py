"""Read-only: individual fills (trades) for the current session.

A Trade is a single leg — one BUY or one SELL. The round-trip lives in the
Position table. The dashboard uses this for the fill log and P&L audit.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.tenant import Tenant
from app.models.trade import Trade
from app.services.tenant import session_service
from app.util.dates import iso_utc

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def list_trades(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(100, le=500),
    session_id: int | None = None,
) -> list[dict]:
    q = db.query(Trade).filter(Trade.tenant_id == tenant.id)
    if session_id is None:
        session = session_service.get_active_session(db, tenant.id)
        session_id = session.id if session else None
    if session_id is not None:
        q = q.filter(Trade.session_id == session_id)

    rows = q.order_by(desc(Trade.created_at)).limit(limit).all()
    return [
        {
            "id": r.id,
            "session_id": r.session_id,
            "position_id": r.position_id,
            "symbol": r.symbol,
            "side": r.side,
            "order_type": r.order_type,
            "price": r.price,
            "quantity": r.quantity,
            "fee": r.fee,
            "status": r.status,
            "created_at": iso_utc(r.created_at),
            "filled_at": iso_utc(r.filled_at),
        }
        for r in rows
    ]
