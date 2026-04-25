"""Technical-indicator computation. Pure Python, no numpy/scipy.

Reads closed candles from the DB and returns a small dataclass of the
indicators the AI prompt needs. Cheap to run every cycle.

Trend label rule:
    - "bullish"  if EMA9 > EMA21 and price_change_pct > 0
    - "bearish"  if EMA9 < EMA21 and price_change_pct < 0
    - "neutral"  otherwise
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import desc

from app.models.candle import Candle

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class TechnicalReading:
    symbol: str
    interval: str
    ema_9: float | None = None
    ema_21: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    volume_change_pct: float | None = None
    price_change_pct: float | None = None
    trend_label: str = "neutral"
    samples: int = 0


def _ema(values: list[float], period: int) -> list[float]:
    """Standard exponential moving average. Seeds with a simple average of
    the first `period` values, then applies the EMA recurrence."""
    if len(values) < period or period <= 0:
        return []
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(values: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None if not enough data."""
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 4)


def _macd(values: list[float]) -> tuple[float | None, float | None]:
    """Standard MACD: 12-26 EMA difference, signal = 9-EMA of MACD line."""
    if len(values) < 35:
        return None, None
    ema12 = _ema(values, 12)
    ema26 = _ema(values, 26)
    if not ema12 or not ema26:
        return None, None
    # Align lengths (ema26 starts later than ema12).
    offset = len(ema12) - len(ema26)
    macd_line = [a - b for a, b in zip(ema12[offset:], ema26)]
    signal_line = _ema(macd_line, 9)
    if not signal_line:
        return round(macd_line[-1], 6), None
    return round(macd_line[-1], 6), round(signal_line[-1], 6)


def compute(
    db: "Session",
    symbol: str,
    *,
    interval: str | None = None,
    samples: int = 60,
) -> TechnicalReading:
    """Read up to `samples` recent closed candles and compute indicators.
    Falls back gracefully if there isn't enough history yet."""
    symbol = symbol.upper()
    if interval is None:
        latest = (
            db.query(Candle)
            .filter(Candle.symbol == symbol, Candle.is_closed == True)  # noqa: E712
            .order_by(desc(Candle.open_time))
            .first()
        )
        if latest is None:
            return TechnicalReading(symbol=symbol, interval="?")
        interval = latest.interval

    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.interval == interval,
            Candle.is_closed == True,  # noqa: E712
        )
        .order_by(desc(Candle.open_time))
        .limit(samples)
        .all()
    )
    if not rows:
        return TechnicalReading(symbol=symbol, interval=interval)

    rows = list(reversed(rows))  # oldest-first
    closes = [float(r.close) for r in rows]
    volumes = [float(r.volume) for r in rows]

    out = TechnicalReading(symbol=symbol, interval=interval, samples=len(closes))
    if len(closes) >= 2:
        out.price_change_pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 4) if closes[-2] else None
    if len(volumes) >= 2 and volumes[-2]:
        out.volume_change_pct = round((volumes[-1] - volumes[-2]) / volumes[-2] * 100, 4)

    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    out.ema_9 = round(ema9[-1], 4) if ema9 else None
    out.ema_21 = round(ema21[-1], 4) if ema21 else None
    out.rsi_14 = _rsi(closes, 14)
    out.macd, out.macd_signal = _macd(closes)

    # Trend label
    if out.ema_9 is not None and out.ema_21 is not None and out.price_change_pct is not None:
        if out.ema_9 > out.ema_21 and out.price_change_pct > 0:
            out.trend_label = "bullish"
        elif out.ema_9 < out.ema_21 and out.price_change_pct < 0:
            out.trend_label = "bearish"
        else:
            out.trend_label = "neutral"

    return out
