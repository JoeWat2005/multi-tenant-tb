"""Per-session equity curve samples.

Written on every fill and on a periodic heartbeat during RUNNING state so
the dashboard can draw a smooth balance curve for the current session.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True,
    )

    balance: Mapped[float] = mapped_column(Float)           # cash on hand
    unrealised_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realised_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_equity: Mapped[float] = mapped_column(Float)      # balance + unrealised

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
