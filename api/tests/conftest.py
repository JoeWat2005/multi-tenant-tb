"""Pytest fixtures: in-memory SQLite + a Session per test, with all model
tables created from the same Base used by app.db.session. Shared factory
fixtures for tenants, configs, sessions, candles, snapshots."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# Make `app.*` importable when pytest is run from the api/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Importing the package registers every model on Base.metadata.
import app.models  # noqa: F401
from app.db.session import Base
from app.models.bot_config import BotConfig
from app.models.candle import Candle
from app.models.tenant import Tenant
from app.models.trading_session import TradingSession
from app.models.user import User
from app.services.tenant import session_service


# ── Engine / DB session ───────────────────────────────────────────────────


class _NoCloseSession:
    """Proxy around a SQLAlchemy Session whose `close()` is a no-op.

    Several production handlers (ExecutionWorker, OcoTriggerService, etc.)
    open a SessionLocal in a try/finally and `close()` it at the end. When
    a test patches SessionLocal to hand back the fixture's session, that
    close() would kill the session before the test gets to assert on it.
    Wrapping with this proxy lets the handler think it's managing a
    fresh session while keeping the fixture's session alive."""

    def __init__(self, session):
        object.__setattr__(self, "_session", session)

    def close(self):
        # Pretend to close — actually just flush so any pending writes
        # land before the test asserts on them.
        self._session.flush()

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __setattr__(self, name, value):
        setattr(self._session, name, value)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def session_factory(db):
    """Returns a zero-arg callable that hands handlers a proxy session
    sharing the test's DB but ignoring close() so assertions still work."""
    def _factory():
        return _NoCloseSession(db)
    return _factory


# ── Factory fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def make_tenant(db):
    """Return a callable that builds a User + Tenant pair."""
    counter = {"i": 0}

    def _make(*, email: str | None = None) -> Tenant:
        counter["i"] += 1
        i = counter["i"]
        user = User(clerk_user_id=f"clerk_user_{i}", email=email or f"user{i}@example.com")
        db.add(user)
        db.flush()
        tenant = Tenant(user_id=user.id)
        db.add(tenant)
        db.commit()
        return tenant

    return _make


@pytest.fixture()
def tenant(make_tenant):
    return make_tenant()


@pytest.fixture()
def bot_config(db, tenant):
    cfg = BotConfig(
        tenant_id=tenant.id,
        symbol="BTC/USD",
        interval="30m",
        mode="PAPER",
        position_size_pct=10.0,
        min_confidence=0.65,
        stop_loss_pct=2.0,
        take_profit_pct=4.0,
        max_session_loss_pct=3.0,
        paper_balance_usd=10_000.0,
    )
    db.add(cfg)
    db.commit()
    return cfg


@pytest.fixture()
def running_session(db, tenant, bot_config):
    sess = TradingSession(
        tenant_id=tenant.id,
        config_id=bot_config.id,
        symbol="BTC/USD",
        interval="30m",
        mode="PAPER",
        status=session_service.RUNNING,
        starting_balance=10_000.0,
        current_balance=10_000.0,
    )
    db.add(sess)
    db.commit()
    return sess


@pytest.fixture()
def seeded_candles(db):
    """Seed 30 1h candles for BTC/USD in a clear uptrend."""
    base = datetime.now(timezone.utc) - timedelta(hours=30)
    for i in range(30):
        db.add(Candle(
            symbol="BTC/USD",
            interval="30m",
            open_time=base + timedelta(minutes=30 * i),
            close_time=base + timedelta(minutes=30 * (i + 1)),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
            volume=10.0 + i * 0.1,
            is_closed=True,
        ))
    db.commit()


# pytest-asyncio is configured via pytest.ini (`asyncio_mode = auto`).
