"""Alpaca crypto market WebSocket — single persistent connection.

Streams quotes and trades for the union of every running tenant's
symbol, and feeds MarketStateStore + publishes price_tick events on
the EventBus. Paper and live share the same market-data endpoint, so
there's nothing mode-specific here.

Auth: the first message after connect is `{action: "auth", key, secret}`.
Subscribe: `{action: "subscribe", quotes: [...], trades: [...]}`.
Messages are arrays of events; each event has a `T` type field.

Reconnect is automatic with exponential backoff. Calling `reconnect()`
after changing the subscription closes the current socket so the outer
loop rebuilds with the fresh symbol set.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import websockets

from app.config import settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.event_bus import EventBus
    from app.services.shared.market_state import MarketStateStore


ALPACA_CRYPTO_STREAM = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"


class AlpacaMarketWS:
    def __init__(
        self,
        market_store: "MarketStateStore",
        event_bus: "EventBus",
    ) -> None:
        self.market_store = market_store
        self.event_bus = event_bus
        self._symbols: set[str] = set()
        self._ws: websockets.ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._backoff = 1.0

    # ── Public API ────────────────────────────────────────────────────────

    def set_subscription(self, symbols: set[str]) -> None:
        self._symbols = {_normalise(s) for s in symbols if s}

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_forever(), name="alpaca-market-ws")
        logger.info("AlpacaMarketWS started")

    async def stop(self) -> None:
        self._stop.set()
        await self._close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def reconnect(self) -> None:
        """Close the current connection so the outer loop reauthenticates
        and re-subscribes with the latest symbol set."""
        await self._close()

    # ── Loop ──────────────────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            if not self._symbols:
                await asyncio.sleep(2)
                continue
            if not settings.alpaca_api_key or not settings.alpaca_api_secret:
                logger.warning("AlpacaMarketWS: keys not configured — sleeping")
                await asyncio.sleep(10)
                continue
            try:
                async with websockets.connect(
                    ALPACA_CRYPTO_STREAM, ping_interval=20, ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._backoff = 1.0
                    if not await self._auth_and_subscribe(ws):
                        continue
                    logger.info("Alpaca WS connected (%s symbols)", len(self._symbols))
                    async for raw in ws:
                        self._handle_message(raw if isinstance(raw, str) else raw.decode())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Alpaca WS error: %s — reconnecting in %.1fs", exc, self._backoff)
                try:
                    await asyncio.sleep(self._backoff)
                except asyncio.CancelledError:
                    raise
                self._backoff = min(self._backoff * 2, 30.0)
            finally:
                self._ws = None

    async def _close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _auth_and_subscribe(self, ws: websockets.ClientConnection) -> bool:
        # Alpaca sends a "connected" frame first — ignore it.
        await ws.recv()

        await ws.send(json.dumps({
            "action": "auth",
            "key": settings.alpaca_api_key,
            "secret": settings.alpaca_api_secret,
        }))
        auth_ack = await ws.recv()
        if "authenticated" not in (auth_ack if isinstance(auth_ack, str) else auth_ack.decode()):
            logger.error("Alpaca WS auth failed: %s", auth_ack)
            return False

        symbols = sorted(self._symbols)
        await ws.send(json.dumps({
            "action": "subscribe",
            "quotes": symbols,
            "trades": symbols,
        }))
        return True

    # ── Parsing ───────────────────────────────────────────────────────────

    def _handle_message(self, raw: str) -> None:
        try:
            events = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(events, list):
            return
        for ev in events:
            try:
                self._handle_event(ev)
            except Exception:
                logger.exception("Alpaca WS event handling failed: %s", ev)

    def _handle_event(self, ev: dict) -> None:
        t = ev.get("T")
        symbol = ev.get("S")
        if not symbol:
            return
        symbol = _normalise(symbol)

        if t == "q":
            # Quote: bp/ap = bid/ask price, bs/as = sizes
            bid = _f(ev.get("bp"))
            ask = _f(ev.get("ap"))
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            self.market_store.update(
                symbol,
                last_price=mid if mid > 0 else None,
                bid=bid if bid > 0 else None,
                ask=ask if ask > 0 else None,
            )
        elif t == "t":
            # Trade: p = price, s = size
            price = _f(ev.get("p"))
            if price > 0:
                self.market_store.update(symbol, last_price=price)
        elif t == "b":
            # Bar (minute bar): close price is a good last-price fallback
            close = _f(ev.get("c"))
            if close > 0:
                self.market_store.update(
                    symbol,
                    last_price=close,
                    high_24h=_f(ev.get("h")) or None,
                    low_24h=_f(ev.get("l")) or None,
                    volume_24h=_f(ev.get("v")) or None,
                )


def _normalise(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if "/" in s:
        return s
    if s.endswith("USDT"):
        return f"{s[:-4]}/USD"
    if s.endswith("USD"):
        return f"{s[:-3]}/USD"
    return s


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
