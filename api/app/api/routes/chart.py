"""Chart data for the symbol-detail screen.

Candles come from shared data_feeds (same for every tenant). Entry/exit
markers come from this tenant's Trade history for the symbol."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import current_tenant, get_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/chart", tags=["chart"])


@router.get("/{symbol}")
def get_chart(
    symbol: str,
    tenant: Annotated[Tenant, Depends(current_tenant)],
    db: Annotated[Session, Depends(get_db)],
    interval: str = Query("1h"),
    limit: int = Query(200, le=1000),
) -> dict:
    # TODO: candles = data_feeds.fetch_ohlcv(symbol, interval, limit)  (shared)
    # TODO: markers = Trade WHERE tenant_id AND symbol (entry + exit timestamps/prices)
    # TODO: position = risk_service.get_open_positions().get(symbol) if any
    return {"candles": [], "markers": [], "position": None}
