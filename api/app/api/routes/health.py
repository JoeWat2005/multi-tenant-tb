"""Liveness + readiness. Not auth-gated — used by Docker/platform healthchecks."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"status": "ok"}
