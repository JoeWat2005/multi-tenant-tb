"""A trading entity. In v1 multi-tenant this is 1:1 with User; kept as a
separate table so we can later support teams / shared tenants without schema
churn.

Every row in `bot_configs`, `ai_decisions`, `executions`, `trades`, and
`trading_sessions` carries a `tenant_id` FK back here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
