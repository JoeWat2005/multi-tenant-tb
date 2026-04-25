"""An open-to-close position round-trip.

Created by PositionService when a BUY fill is confirmed; updated on every
tick (`unrealised_pnl`) and finalised when the exit SELL fills.

Exit reasons: TAKE_PROFIT | STOP_LOSS | USER_STOP | AI_SELL | ERROR_EXIT.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True,
    )

    symbol: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open | closed

    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float)                  # base asset qty
    position_value: Mapped[float] = mapped_column(Float)            # USD notional at entry
    size_pct: Mapped[float] = mapped_column(Float)                  # % of starting_balance
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    unrealised_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realised_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
