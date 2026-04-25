"""Tests for the technical-indicator helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.services.shared import technical_service
from app.services.shared.technical_service import _ema, _macd, _rsi


def test_ema_first_value_is_simple_average():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = _ema(closes, period=3)
    # First seed is mean(1,2,3) = 2.0
    assert abs(out[0] - 2.0) < 1e-9
    # Subsequent values keep increasing toward the latest input.
    assert out[1] > out[0]
    assert out[2] > out[1]


def test_rsi_constant_uptrend_is_high():
    closes = [float(i) for i in range(1, 25)]
    rsi = _rsi(closes, period=14)
    assert rsi == 100.0  # only gains, no losses


def test_rsi_constant_downtrend_is_low():
    closes = [float(i) for i in range(24, 0, -1)]
    rsi = _rsi(closes, period=14)
    assert rsi is not None
    assert rsi < 5.0


def test_macd_returns_two_floats_when_enough_data():
    closes = [100 + i * 0.1 for i in range(60)]
    macd, signal = _macd(closes)
    assert macd is not None and signal is not None


def test_compute_falls_back_when_no_candles(db):
    reading = technical_service.compute(db, "BTC/USD")
    # No candles exist at all → most fields are None, samples=0.
    assert reading.samples == 0
    assert reading.ema_9 is None


def test_compute_produces_indicators_when_data_exists(db):
    # Seed 40 closing prices in a clear uptrend.
    base = datetime.now(timezone.utc) - timedelta(hours=40)
    for i in range(40):
        c = Candle(
            symbol="BTC/USD",
            interval="1h",
            open_time=base + timedelta(hours=i),
            close_time=base + timedelta(hours=i + 1),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
            volume=10.0 + i * 0.1,
            is_closed=True,
        )
        db.add(c)
    db.commit()

    reading = technical_service.compute(db, "BTC/USD")
    assert reading.samples >= 30
    assert reading.ema_9 is not None and reading.ema_21 is not None
    assert reading.rsi_14 is not None and reading.rsi_14 > 70  # strong uptrend
    assert reading.trend_label == "bullish"
