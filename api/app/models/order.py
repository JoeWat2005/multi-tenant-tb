"""Pending or active exchange orders.

Distinct from Trade (which records what filled). An Order represents an
instruction currently with the exchange: a market order in flight, an OCO
stop/take pair waiting to trigger, or a cancelled order waiting for
confirmation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True,
    )
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("positions.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    symbol: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)              # BUY | SELL
    type: Mapped[str] = mapped_column(String)              # MARKET | LIMIT | STOP_LOSS | TAKE_PROFIT
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    # pending | submitted | active | filled | cancelled | rejected

    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)        # limit / target price
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)   # stop trigger

    alpaca_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Groups the two legs of a local SL/TP pair so we can cancel them
    # together. Alpaca crypto doesn't support server-side OCO/bracket, so
    # this is a client-assigned uuid, not an exchange id.
    oco_group_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
