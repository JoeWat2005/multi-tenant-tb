"""SQLAlchemy engine + SessionLocal factory.

Used by:
  - FastAPI request handlers via the `get_db` dependency
  - Background services that need their own short-lived sessions
    (execution_worker, tick_scheduler) — they call `SessionLocal()` directly.

SQLite quirks handled here:
  - `check_same_thread=False` so threads in asyncio.to_thread can share the
    engine safely (each opens its own Session).
  - `PRAGMA foreign_keys=ON` on every new connection — SQLite ships with
    FK enforcement OFF by default, which would silently ignore our
    `ON DELETE CASCADE` declarations.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if _is_sqlite:
    @event.listens_for(Engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass
