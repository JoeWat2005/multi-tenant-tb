"""A raw sentiment reading. Never overwritten — old snapshots fade out
via the recency-weighted score computed by sentiment_service."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SentimentSnapshot(Base):
    __tablename__ = "sentiment_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    symbol: Mapped[str] = mapped_column(String, index=True)
    score: Mapped[float] = mapped_column(Float)      # -1.0 to +1.0
    label: Mapped[str] = mapped_column(String)       # bullish | bearish | neutral
    source: Mapped[str] = mapped_column(String, index=True)
    # source ∈ {rss, alpaca_news, fear_greed, price_action}

    # Source weight applied at computation time (e.g. alpaca_news=1.3). Stored
    # so we can recompute weighted_score historically with the same weights
    # the bot actually saw.
    source_weight: Mapped[float] = mapped_column(Float, default=1.0)

    # SHA-256 of the full raw_payload — used to skip writes when nothing changed.
    payload_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # SHA-256 of the deduplicated set of normalised headlines/article ids only.
    # Lets us catch the case where ordering changed but content is identical.
    normalized_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Number of articles/items that contributed to this snapshot's score.
    # Useful for weighting in downstream consumers.
    article_count: Mapped[int] = mapped_column(Integer, default=0)

    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional structured metadata (per-feed counts, error notes, etc.).
    snapshot_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
