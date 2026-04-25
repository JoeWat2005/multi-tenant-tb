"""Tests for the lazy-create + race-safe `_get_or_create_user` /
`_get_or_create_tenant` helpers in app.auth.dependencies."""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.auth import dependencies as deps
from app.models.tenant import Tenant
from app.models.user import User


def test_get_or_create_user_creates_new(db):
    user = deps._get_or_create_user(db, "clerk_abc", "abc@example.com")
    assert user.id is not None
    assert user.clerk_user_id == "clerk_abc"


def test_get_or_create_user_returns_existing(db):
    first = deps._get_or_create_user(db, "clerk_abc", "abc@example.com")
    second = deps._get_or_create_user(db, "clerk_abc", "different@example.com")
    assert first.id == second.id


def test_get_or_create_user_handles_integrity_race(db, monkeypatch):
    """Simulate the parallel-request race: another transaction inserted the
    row between our SELECT and our INSERT. We should rollback and re-read."""
    # Pre-create the user as if a parallel handler had committed it.
    db.add(User(clerk_user_id="clerk_race", email="race@x.com"))
    db.commit()

    # Force the SELECT to first miss, then succeed.
    real_query = db.query
    call_state = {"first": True}

    def fake_query(model):
        if model is User and call_state["first"]:
            call_state["first"] = False

            class Q:
                def filter(self, *a, **kw): return self
                def first(self): return None

            return Q()
        return real_query(model)

    monkeypatch.setattr(db, "query", fake_query)

    # Now the helper will think there's no user, try INSERT, hit UNIQUE
    # constraint, then re-query (which uses the real query path).
    user = deps._get_or_create_user(db, "clerk_race", "race@x.com")
    assert user.clerk_user_id == "clerk_race"


def test_get_or_create_tenant_creates_and_returns(db):
    user = deps._get_or_create_user(db, "clerk_t", "t@example.com")
    tenant = deps._get_or_create_tenant(db, user.id)
    assert tenant.id is not None
    assert tenant.user_id == user.id

    # Idempotent
    again = deps._get_or_create_tenant(db, user.id)
    assert again.id == tenant.id
