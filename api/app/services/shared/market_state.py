"""Shared in-memory store of live ticker data.

Fed by AlpacaMarketWS. Read by ExecutionWorker, OcoTriggerService, the
dashboard WS push (Phase 5), and trading_cycle for decision-time prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Iterable


@dataclass
class SymbolMarketState:
    symbol: str
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume_24h: float | None = None
    price_change_24h_pct: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None


class MarketStateStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state: dict[str, SymbolMarketState] = {}

    def update(self, symbol: str, **fields) -> None:
        symbol = symbol.upper()
        with self._lock:
            state = self._state.setdefault(symbol, SymbolMarketState(symbol=symbol))
            for k, v in fields.items():
                if v is not None:
                    setattr(state, k, v)

    def get(self, symbol: str) -> SymbolMarketState | None:
        with self._lock:
            return self._state.get(symbol.upper())

    def last_price(self, symbol: str) -> float | None:
        with self._lock:
            st = self._state.get(symbol.upper())
            return st.last_price if st else None

    def snapshot(self, symbols: Iterable[str] | None = None) -> dict[str, SymbolMarketState]:
        with self._lock:
            if symbols is None:
                return dict(self._state)
            return {s.upper(): self._state[s.upper()] for s in symbols if s.upper() in self._state}
