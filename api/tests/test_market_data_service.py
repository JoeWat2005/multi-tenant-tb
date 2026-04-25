"""Tests for market_data_service — refresh idempotency + interval helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.shared import market_data_service


def test_interval_minutes_known():
    assert market_data_service.interval_minutes("30m") == 30
    assert market_data_service.interval_minutes("1h") == 60
    assert market_data_service.interval_minutes("4h") == 240
    assert market_data_service.interval_minutes("1d") == 1440


def test_interval_minutes_unknown_returns_none():
    assert market_data_service.interval_minutes("7m") is None
    assert market_data_service.interval_minutes("") is None


def test_refresh_inserts_closed_candles_only(db, monkeypatch):
    """Build a synthetic OHLCV response with one in-progress candle at the
    end and verify it gets dropped."""
    now = datetime.now(timezone.utc)
    minutes = 30

    def _ts(offset_steps: int) -> int:
        # offset_steps in minutes back from now (negative = past)
        return int((now + timedelta(minutes=offset_steps)).timestamp() * 1000)

    fake = [
        # 4 closed bars (close_time in the past)
        [_ts(-minutes * 5), 100, 101, 99, 100, 1.0],
        [_ts(-minutes * 4), 100, 102, 99, 101, 1.0],
        [_ts(-minutes * 3), 101, 103, 100, 102, 1.0],
        [_ts(-minutes * 2), 102, 104, 101, 103, 1.0],
        # 1 in-progress bar (open_time = now-30m, close_time = now → equal, OK,
        # so push it explicitly to the future).
        [int((now - timedelta(minutes=10)).timestamp() * 1000), 103, 105, 102, 104, 1.0],
    ]

    def fake_fetch(symbol, *, timeframe, limit):
        return fake

    monkeypatch.setattr(market_data_service.data_feeds, "fetch_ohlcv", fake_fetch)

    inserted = market_data_service.refresh(db, "BTC/USD", "30m")
    assert inserted == 4

    # Calling refresh again with the same data should be a no-op (UNIQUE
    # constraint on (symbol, interval, open_time)).
    inserted2 = market_data_service.refresh(db, "BTC/USD", "30m")
    assert inserted2 == 0


def test_latest_returns_oldest_first(db):
    from app.models.candle import Candle

    base = datetime.now(timezone.utc) - timedelta(hours=5)
    for i in range(5):
        db.add(Candle(
            symbol="BTC/USD", interval="30m",
            open_time=base + timedelta(minutes=30 * i),
            close_time=base + timedelta(minutes=30 * (i + 1)),
            open=100, high=101, low=99, close=100 + i, volume=1.0, is_closed=True,
        ))
    db.commit()

    rows = market_data_service.latest(db, "BTC/USD", "30m", n=5)
    assert len(rows) == 5
    assert rows[0].close < rows[-1].close  # oldest first
