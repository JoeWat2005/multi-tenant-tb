"""One row per AI decision inside a session.

`approved` + `rejection_reason` record what the RiskService did with the raw
AI verdict: True means we queued an order, False means HOLD-by-force.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BotDecision(Base):
    __tablename__ = "bot_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True,
    )

    symbol: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)              # BUY | SELL | HOLD
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")

    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    weighted_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)

    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
