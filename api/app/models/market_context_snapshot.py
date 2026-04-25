"""Frozen snapshot of the full context that fed one trading-cycle decision.

Persisted by MarketContextService.build() so we can replay any decision —
the AI saw exactly what's stored here. `decision_id` is filled in after
the BotDecision row is committed in the same cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class MarketContextSnapshot(Base):
    __tablename__ = "market_context_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True,
    )
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("bot_decisions.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    symbol: Mapped[str] = mapped_column(String, index=True)
    interval: Mapped[str] = mapped_column(String)

    weighted_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # JSON-encoded text columns (SQLite-friendly). Each column captures a
    # different slice of the input the AI saw, so a future replay or audit
    # can reconstruct the full prompt.
    source_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_news_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    fear_greed_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    technical_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_memory_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
