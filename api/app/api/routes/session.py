"""Current-session read endpoints for the dashboard."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.equity_snapshot import EquitySnapshot
from app.models.position import Position
from app.models.tenant import Tenant
from app.models.trading_session import TradingSession
from app.services.tenant import session_service
from app.util.dates import iso_utc

router = APIRouter(prefix="/api/session", tags=["session"])


@router.get("/current")
def current(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Active non-terminal session if there is one, else the most recent
    terminal session (ENDED / ERROR) so the UI can show the "last run" P&L
    until a new session starts."""
    session = session_service.get_active_session(db, tenant.id)
    if session is None:
        session = (
            db.query(TradingSession)
            .filter(TradingSession.tenant_id == tenant.id)
            .order_by(desc(TradingSession.id))
            .first()
        )
    if session is None:
        return {"session": None}

    open_pos_count = (
        db.query(Position)
        .filter(Position.session_id == session.id, Position.status == "open")
        .count()
    )
    return {
        "session": {
            "id": session.id,
            "status": session.status,
            "symbol": session.symbol,
            "interval": session.interval,
            "mode": session.mode,
            "starting_balance": session.starting_balance,
            "current_balance": session.current_balance,
            "ending_balance": session.ending_balance,
            "session_pnl": session.session_pnl,
            "session_pnl_pct": session.session_pnl_pct,
            "open_positions": open_pos_count,
            "started_at": iso_utc(session.started_at),
            "stop_requested_at": iso_utc(session.stop_requested_at),
            "ended_at": iso_utc(session.ended_at),
            "error_message": session.error_message,
        }
    }


@router.get("/equity")
def equity(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 500,
) -> list[dict]:
    """Equity curve for the current (or last) session."""
    session = session_service.get_active_session(db, tenant.id) or (
        db.query(TradingSession)
        .filter(TradingSession.tenant_id == tenant.id)
        .order_by(desc(TradingSession.id))
        .first()
    )
    if session is None:
        return []
    rows = (
        db.query(EquitySnapshot)
        .filter(EquitySnapshot.session_id == session.id)
        .order_by(EquitySnapshot.created_at.asc())
        .limit(limit)
        .all()
    )
    return [
        {
            "balance": r.balance,
            "unrealised_pnl": r.unrealised_pnl,
            "realised_pnl": r.realised_pnl,
            "total_equity": r.total_equity,
            "created_at": iso_utc(r.created_at),
        }
        for r in rows
    ]
