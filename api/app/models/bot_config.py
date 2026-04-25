"""Per-tenant bot configuration. One row per tenant.

The `running` flag + `next_run_at` are the runtime state owned by the
session/tick-scheduler pair; everything else is user-configurable parameters
that the UI writes via `PUT /api/bot/config`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BotConfig(Base):
    __tablename__ = "bot_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, index=True,
    )

    # Runtime state — owned by session_service + tick_scheduler
    running: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Market + cadence
    symbol: Mapped[str] = mapped_column(String, default="BTC/USD")
    interval: Mapped[str] = mapped_column(String, default="30m")  # candle interval for decisions; allowed: 30m, 1h, 4h
    mode: Mapped[str] = mapped_column(String, default="PAPER")    # PAPER | LIVE

    # Risk params
    position_size_pct: Mapped[float] = mapped_column(Float, default=10.0)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.65)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=2.0)
    take_profit_pct: Mapped[float] = mapped_column(Float, default=4.0)
    max_session_loss_pct: Mapped[float] = mapped_column(Float, default=3.0)

    # Feature toggles + context size
    sentiment_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)
    decision_lookback_candles: Mapped[int] = mapped_column(Integer, default=50)
    use_sentiment: Mapped[bool] = mapped_column(Boolean, default=True)
    use_ai: Mapped[bool] = mapped_column(Boolean, default=True)

    # Per-tenant virtual starting balance in USD (independent of Alpaca's
    # account cash — every tenant gets their own synthetic starting_balance
    # even though the real orders all settle against the same Alpaca paper
    # account).
    paper_balance_usd: Mapped[float] = mapped_column(Float, default=10_000.0)
    model_name: Mapped[str] = mapped_column(String, default="claude-sonnet-4-6")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
