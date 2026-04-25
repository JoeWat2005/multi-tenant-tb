"""Clerk JWT validation.

The browser sends `Authorization: Bearer <clerk_jwt>`. We:
  1. Fetch Clerk's JWKS (cached in-memory).
  2. Verify signature + issuer + expiry.
  3. Return the decoded claims — `sub` is the Clerk user id.

Tenant resolution (clerk_user_id -> tenant_id) happens in dependencies.py,
not here, so this module stays pure.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)

_jwk_client: PyJWKClient | None = None


def _get_jwk_client() -> PyJWKClient:
    """Lazy-init the JWKS client. PyJWKClient caches keys internally."""
    global _jwk_client
    if _jwk_client is None:
        if not settings.clerk_jwks_url:
            raise RuntimeError("CLERK_JWKS_URL not configured")
        _jwk_client = PyJWKClient(settings.clerk_jwks_url)
    return _jwk_client


def verify_token(token: str) -> dict[str, Any]:
    """Verify a Clerk-issued JWT. Returns the decoded claims on success.

    Raises jwt.InvalidTokenError (or subclass) on any verification failure —
    the caller should map this to a 401 response.
    """
    signing_key = _get_jwk_client().get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        issuer=settings.clerk_issuer or None,
        options={"verify_aud": False},  # Clerk JWTs have no audience by default
    )
    return claims
