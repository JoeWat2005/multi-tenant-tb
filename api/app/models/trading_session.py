"""A trading session: one Start → Stop run for a tenant.

Statuses form a strict lifecycle owned by SessionService:

    IDLE -> STARTING -> RUNNING -> STOPPING -> EXITING -> ENDED
                              ↘ ERROR

Only one non-terminal session exists per tenant at a time. Historical sessions
(status in {ENDED, ERROR}) accumulate for review/backtesting.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TradingSession(Base):
    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    config_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_configs.id", ondelete="SET NULL"), nullable=True,
    )

    symbol: Mapped[str] = mapped_column(String, default="BTC/USD")
    interval: Mapped[str] = mapped_column(String, default="30m")
    mode: Mapped[str] = mapped_column(String, default="PAPER")  # PAPER | LIVE

    # IDLE | STARTING | RUNNING | STOPPING | EXITING | ENDED | ERROR
    status: Mapped[str] = mapped_column(String, default="IDLE", index=True)

    starting_balance: Mapped[float] = mapped_column(Float, default=0.0)
    current_balance: Mapped[float] = mapped_column(Float, default=0.0)
    ending_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    session_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    session_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
