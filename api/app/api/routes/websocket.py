"""Per-user WebSocket for real-time push.

Browser connects with ?token=<clerk_jwt>. We validate the JWT the same way
the REST routes do, resolve the tenant, then subscribe to the EventBus
channel for that tenant_id. Events: new_decision | trade_opened |
trade_closed | price_tick.

Kept separate from alpaca_ws (which is an outgoing connection to Alpaca) —
this is the incoming connection from the user's browser.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.auth.clerk import verify_token
from app.db.session import SessionLocal
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["ws"])


@router.websocket("")
async def tenant_ws(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    # 1. Auth: validate Clerk JWT supplied as query param (WebSocket has no
    #    Authorization header support in browsers).
    try:
        claims = verify_token(token)
    except InvalidTokenError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. Resolve tenant_id. Short-lived session — just read, don't hold.
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.clerk_user_id == claims["sub"]).first()
        tenant = db.query(Tenant).filter(Tenant.user_id == user.id).first() if user else None
    finally:
        db.close()

    if tenant is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    # TODO: queue = event_bus.subscribe(tenant.id)
    # TODO: pump events to websocket.send_json(event) in a loop
    # TODO: on WebSocketDisconnect -> event_bus.unsubscribe(tenant.id, queue)
    try:
        while True:
            # TODO: replace with await queue.get()
            msg = await websocket.receive_text()
            _ = msg
    except WebSocketDisconnect:
        pass
