"""AI abstraction — lets us swap Claude for a local open-source model without
touching the trading cycle.

DecisionContext is a pure dataclass: no tenant_id, no DB session. All
tenant-specific data is flattened into it by the caller. This keeps providers
trivially testable and swappable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MarketSnapshot:
    symbol: str
    last_price: float
    bid: float
    ask: float
    high_24h: float
    low_24h: float
    change_24h_pct: float
    volume_24h: float
    recent_closes: list[float] = field(default_factory=list)  # last ~6 hourly closes


@dataclass
class PositionView:
    symbol: str
    entry_price: float
    size_pct: float
    current_price: float
    unrealized_pnl_pct: float


@dataclass
class SentimentView:
    fear_greed_index: float | None = None
    per_symbol: dict[str, float] = field(default_factory=dict)   # symbol -> score
    headlines: dict[str, list[str]] = field(default_factory=dict)  # symbol -> top headlines


@dataclass
class PreviousDecision:
    symbol: str
    action: str
    confidence: float
    reasoning: str


@dataclass
class DecisionContext:
    markets: list[MarketSnapshot]
    positions: list[PositionView]
    sentiment: SentimentView
    previous_decisions: list[PreviousDecision]
    # Risk thresholds surfaced into the prompt so Claude's recommendations
    # align with what the risk layer will accept.
    min_confidence: float
    max_position_pct: float


@dataclass
class Decision:
    symbol: str
    action: str          # BUY | SELL | HOLD
    confidence: float
    size_pct: float
    reasoning: str
    # AI-suggested SL/TP (percent). Risk service still has the final say
    # and may clamp/override based on BotConfig.
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0


class DecisionProvider(Protocol):
    """Any class that can turn a context into a Decision. Concrete providers
    take a MarketContext + risk thresholds (kept loose-typed at the protocol
    level so we don't import the heavy module here)."""

    def name(self) -> str: ...
    async def decide(
        self, context: Any, *, min_confidence: float, max_position_pct: float,
    ) -> Decision: ...
