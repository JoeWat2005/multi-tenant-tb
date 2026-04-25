"""Clerk webhook receiver.

Relevant events:
  - user.created — create User + Tenant row (belt-and-braces; current_tenant
    lazy-creates too).
  - user.deleted — delete User row. FK cascade (ON DELETE CASCADE on every
    FK back to users/tenants) wipes tenants, bot_configs, trading_sessions,
    positions, trades, orders, bot_decisions, equity_snapshots, candles
    attached to that tenant's sessions.
  - user.updated — sync email address.

Signature verification uses Svix's HMAC-SHA256 scheme:
    signed = f"{svix_id}.{svix_timestamp}.{body}"
    sig    = base64(HMAC_SHA256(secret_bytes, signed))
where secret_bytes is the `whsec_`-prefixed secret base64-decoded.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import time
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import state
from app.auth.dependencies import get_db
from app.config import settings
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/clerk", tags=["webhooks"])


TIMESTAMP_TOLERANCE_SECONDS = 5 * 60


# ── Signature ─────────────────────────────────────────────────────────────


def _verify_svix(
    *, secret: str, svix_id: str, svix_timestamp: str,
    svix_signature: str, body: bytes,
) -> bool:
    """Svix HMAC-SHA256 verification. Returns True iff any `v1,...` token
    in the `svix-signature` header matches."""
    if not (svix_id and svix_timestamp and svix_signature and secret):
        return False

    # Replay guard.
    try:
        ts = int(svix_timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > TIMESTAMP_TOLERANCE_SECONDS:
        return False

    # Secret format: whsec_<base64>
    secret_body = secret.removeprefix("whsec_")
    try:
        secret_bytes = base64.b64decode(secret_body)
    except Exception:
        return False

    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(secret_bytes, signed, sha256).digest()).decode()

    for token in svix_signature.split():
        # Each token: "v1,<base64_sig>"
        version, _, candidate = token.partition(",")
        if version == "v1" and hmac.compare_digest(candidate, expected):
            return True
    return False


# ── Event handlers ────────────────────────────────────────────────────────


def _on_user_created(db: Session, payload: dict) -> None:
    data = payload.get("data") or {}
    clerk_id = data.get("id")
    if not clerk_id:
        return
    email = ""
    emails = data.get("email_addresses") or []
    if emails:
        email = emails[0].get("email_address", "") or ""

    existing = db.query(User).filter(User.clerk_user_id == clerk_id).first()
    if existing:
        return

    user = User(clerk_user_id=clerk_id, email=email)
    db.add(user)
    db.flush()
    db.add(Tenant(user_id=user.id))
    db.commit()
    logger.info("Clerk user.created: %s", clerk_id)


def _on_user_updated(db: Session, payload: dict) -> None:
    data = payload.get("data") or {}
    clerk_id = data.get("id")
    if not clerk_id:
        return
    user = db.query(User).filter(User.clerk_user_id == clerk_id).first()
    if user is None:
        return
    emails = data.get("email_addresses") or []
    if emails:
        user.email = emails[0].get("email_address", user.email) or user.email
        db.commit()


def _on_user_deleted(db: Session, payload: dict) -> None:
    data = payload.get("data") or {}
    clerk_id = data.get("id")
    if not clerk_id:
        return

    user = db.query(User).filter(User.clerk_user_id == clerk_id).first()
    if user is None:
        logger.info("Clerk user.deleted for unknown id=%s — nothing to do", clerk_id)
        return

    # Pull tenant ids first so we can drop them from the in-memory registry
    # after the DB cascade commits.
    tenant_ids = [t.id for t in db.query(Tenant).filter(Tenant.user_id == user.id).all()]

    db.delete(user)  # cascade wipes everything downstream
    db.commit()

    if state.tenant_registry is not None:
        for tid in tenant_ids:
            state.tenant_registry.unregister(tid)

    logger.info(
        "Clerk user.deleted: clerk_id=%s user_id=%s tenants=%s — cascade complete",
        clerk_id, user.id, tenant_ids,
    )


# ── Route ─────────────────────────────────────────────────────────────────


@router.post("")
async def clerk_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[str | None, Header(alias="svix-timestamp")] = None,
    svix_signature: Annotated[str | None, Header(alias="svix-signature")] = None,
) -> dict:
    if not settings.clerk_webhook_secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CLERK_WEBHOOK_SECRET is not configured",
        )

    body = await request.body()
    if not _verify_svix(
        secret=settings.clerk_webhook_secret,
        svix_id=svix_id or "",
        svix_timestamp=svix_timestamp or "",
        svix_signature=svix_signature or "",
        body=body,
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid svix signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body")

    event_type = str(payload.get("type", ""))
    handlers = {
        "user.created": _on_user_created,
        "user.updated": _on_user_updated,
        "user.deleted": _on_user_deleted,
    }
    handler = handlers.get(event_type)
    if handler is not None:
        handler(db, payload)

    return {"ok": True, "event": event_type}
