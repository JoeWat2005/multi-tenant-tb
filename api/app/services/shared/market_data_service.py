"""Closed-candle storage and retrieval.

`refresh(symbol, interval)` pulls the latest N bars from Alpaca, drops
any in-progress bar, and upserts the closed ones into the `candles`
table. Calling it on every cycle is idempotent — the unique
(symbol, interval, open_time) constraint prevents duplicates.

`latest(symbol, interval, n)` returns the N most-recent closed candles,
oldest-first. Used to build the AI prompt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

from app.models.candle import Candle
from app.services.shared import data_feeds

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_INTERVAL_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440,
}


def interval_minutes(interval: str) -> int | None:
    return _INTERVAL_MINUTES.get(interval)


def refresh(
    db: "Session",
    symbol: str,
    interval: str,
    *,
    limit: int = 50,
) -> int:
    """Fetch the last `limit` klines, store the closed ones. Returns number
    of NEW rows inserted (duplicates are silently skipped)."""
    symbol = symbol.upper()
    try:
        raw = data_feeds.fetch_ohlcv(symbol, timeframe=interval, limit=limit + 1)
    except Exception:
        logger.exception("Kline fetch failed (%s %s)", symbol, interval)
        return 0

    minutes = interval_minutes(interval)
    if not minutes:
        logger.warning("Unknown interval %s — skipping candle store", interval)
        return 0

    # data_feeds.fetch_ohlcv returns [[ts_ms, o, h, l, c, v], ...] newest
    # last. Alpaca generally returns closed bars only, but we still guard
    # against an in-progress row whose close_time > now.
    now = datetime.now(timezone.utc)
    inserted = 0
    for row in raw:
        try:
            ts_ms, o, h, l, c, v = row
        except ValueError:
            continue
        open_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        close_time = open_time + timedelta(minutes=minutes)
        if close_time > now:
            # In-progress candle — skip.
            continue

        candle = Candle(
            symbol=symbol, interval=interval,
            open_time=open_time, close_time=close_time,
            open=float(o), high=float(h), low=float(l),
            close=float(c), volume=float(v),
            is_closed=True,
        )
        db.add(candle)
        try:
            db.flush()
            inserted += 1
        except IntegrityError:
            db.rollback()
            # Already have this (symbol, interval, open_time) — ignore.

    if inserted:
        db.commit()
        logger.info("Stored %d closed candles for %s %s", inserted, symbol, interval)
    return inserted


def latest(
    db: "Session",
    symbol: str,
    interval: str,
    n: int,
) -> list[Candle]:
    """Return the N most-recent closed candles, oldest-first (prompt-ready)."""
    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol.upper(),
            Candle.interval == interval,
            Candle.is_closed == True,  # noqa: E712
        )
        .order_by(desc(Candle.open_time))
        .limit(n)
        .all()
    )
    rows.reverse()
    return rows


def latest_close_age_seconds(
    db: "Session",
    symbol: str,
    interval: str,
) -> float | None:
    """How long since the most recent closed candle's close_time.
    Returns None if no closed candle exists for this (symbol, interval)."""
    row = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol.upper(),
            Candle.interval == interval,
            Candle.is_closed == True,  # noqa: E712
        )
        .order_by(desc(Candle.close_time))
        .first()
    )
    if row is None:
        return None
    close = row.close_time
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - close).total_seconds()
