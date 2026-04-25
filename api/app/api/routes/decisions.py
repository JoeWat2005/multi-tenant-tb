"""Read-only: bot decisions for the current session (or filtered)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.bot_decision import BotDecision
from app.models.tenant import Tenant
from app.services.tenant import session_service
from app.util.dates import iso_utc

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("")
def list_decisions(
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(50, le=200),
    session_id: int | None = None,
) -> list[dict]:
    q = db.query(BotDecision).filter(BotDecision.tenant_id == tenant.id)

    # Default to the active (or most recent) session so the dashboard's
    # "decisions feed" only shows this run.
    if session_id is None:
        session = session_service.get_active_session(db, tenant.id)
        session_id = session.id if session else None
    if session_id is not None:
        q = q.filter(BotDecision.session_id == session_id)

    rows = q.order_by(desc(BotDecision.created_at)).limit(limit).all()
    return [
        {
            "id": r.id,
            "session_id": r.session_id,
            "symbol": r.symbol,
            "action": r.action,
            "confidence": r.confidence,
            "reason": r.reason,
            "sentiment_score": r.sentiment_score,
            "weighted_sentiment_score": r.weighted_sentiment_score,
            "price_snapshot": r.price_snapshot,
            "approved": r.approved,
            "rejection_reason": r.rejection_reason,
            "created_at": iso_utc(r.created_at),
        }
        for r in rows
    ]
