"""Alpaca REST helpers. Shared across tenants — crypto market data is
universal and the single Alpaca account serves every paper session.

Symbol format: Alpaca crypto uses "BTC/USD", "ETH/USD", etc. BotConfig
stores the slash form; this module accepts either "BTCUSD" / "BTCUSDT" /
"BTC/USD" and normalises internally for convenience.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from app.config import settings

logger = logging.getLogger(__name__)

_data_client: CryptoHistoricalDataClient | None = None
_trading_clients: dict[bool, TradingClient] = {}


# ── Symbol + interval helpers ─────────────────────────────────────────────


def to_alpaca_symbol(symbol: str) -> str:
    """Normalise any of `BTC/USD` / `BTCUSD` / `BTCUSDT` into `BTC/USD`."""
    s = (symbol or "").upper().strip()
    if "/" in s:
        return s
    if s.endswith("USDT"):
        return f"{s[:-4]}/USD"
    if s.endswith("USD"):
        return f"{s[:-3]}/USD"
    return s


_INTERVAL_TO_TIMEFRAME: dict[str, TimeFrame] = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "2h": TimeFrame(2, TimeFrameUnit.Hour),
    "4h": TimeFrame(4, TimeFrameUnit.Hour),
    "6h": TimeFrame(6, TimeFrameUnit.Hour),
    "12h": TimeFrame(12, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}

_INTERVAL_TO_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
}


def to_alpaca_timeframe(interval: str) -> TimeFrame | None:
    return _INTERVAL_TO_TIMEFRAME.get(interval)


# ── Clients ───────────────────────────────────────────────────────────────


def get_data_client() -> CryptoHistoricalDataClient:
    """Crypto historical data works without API keys, but we pass them when
    configured to raise the per-minute rate limit."""
    global _data_client
    if _data_client is None:
        if settings.alpaca_api_key and settings.alpaca_api_secret:
            _data_client = CryptoHistoricalDataClient(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_api_secret,
            )
        else:
            _data_client = CryptoHistoricalDataClient()
    return _data_client


def get_trading_client(*, paper: bool) -> TradingClient:
    """Lazy-init one TradingClient per (paper=True/False). The global
    `settings.alpaca_paper=true` kill switch forces paper even when a
    tenant session is mode=LIVE."""
    effective_paper = paper or settings.alpaca_paper
    client = _trading_clients.get(effective_paper)
    if client is None:
        if not settings.alpaca_api_key or not settings.alpaca_api_secret:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET not configured")
        client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_api_secret,
            paper=effective_paper,
        )
        _trading_clients[effective_paper] = client
    return client


# ── Market data ───────────────────────────────────────────────────────────


def fetch_ticker(symbol: str) -> dict[str, Any]:
    """Return a ccxt-shaped ticker dict for compatibility with existing callers.
    Alpaca gives us quote (bid/ask) via the latest quote endpoint; we derive
    `last` from the mid if no trade is available."""
    sym = to_alpaca_symbol(symbol)
    try:
        req = CryptoLatestQuoteRequest(symbol_or_symbols=sym)
        quotes = get_data_client().get_crypto_latest_quote(req)
    except Exception:
        logger.exception("Alpaca latest quote failed for %s", sym)
        return {}

    q = quotes.get(sym)
    if q is None:
        return {}

    bid = float(q.bid_price or 0)
    ask = float(q.ask_price or 0)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
    return {
        "symbol": sym,
        "last": mid,
        "bid": bid,
        "ask": ask,
        # 24h stats aren't on the latest-quote endpoint. They're not required
        # by the AI prompt, only the ticker snapshot — leaving zero is fine.
        "high": 0.0,
        "low": 0.0,
        "percentage": 0.0,
        "quoteVolume": 0.0,
    }


def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 50) -> list[list[float]]:
    """Return `[[ts_ms, open, high, low, close, volume], ...]` newest-last,
    matching the old ccxt shape so market_data_service doesn't care."""
    sym = to_alpaca_symbol(symbol)
    tf = to_alpaca_timeframe(timeframe)
    if tf is None:
        logger.warning("Unknown timeframe %s", timeframe)
        return []

    minutes = _INTERVAL_TO_MINUTES.get(timeframe, 15)
    # Pull slightly more than `limit` bars back from now. Alpaca returns
    # closed bars only (no in-progress bar like Binance), so oversampling
    # guards against gaps.
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes * (limit + 5))

    try:
        req = CryptoBarsRequest(
            symbol_or_symbols=sym,
            timeframe=tf,
            start=start,
            end=end,
        )
        bars = get_data_client().get_crypto_bars(req)
    except Exception:
        logger.exception("Alpaca bars fetch failed for %s %s", sym, timeframe)
        return []

    rows = bars.data.get(sym, [])
    rows.sort(key=lambda b: b.timestamp)

    out: list[list[float]] = []
    for b in rows[-limit:]:
        ts_ms = int(b.timestamp.timestamp() * 1000)
        out.append([ts_ms, float(b.open), float(b.high),
                    float(b.low), float(b.close), float(b.volume)])
    return out


def fetch_balance(*, paper: bool = True) -> dict[str, Any]:
    """Account cash + equity for the Alpaca account. Per-tenant synthetic
    balances are tracked separately in `TradingSession.current_balance`."""
    try:
        client = get_trading_client(paper=paper)
    except RuntimeError:
        return {}
    try:
        acct = client.get_account()
    except Exception:
        logger.exception("Alpaca get_account failed")
        return {}
    return {
        "cash": float(acct.cash or 0),
        "equity": float(acct.equity or 0),
        "buying_power": float(acct.buying_power or 0),
    }
