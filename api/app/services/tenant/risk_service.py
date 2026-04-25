"""Validate every AI decision before it becomes an order.

One entry point: `evaluate()`. Returns a RiskCheck with the final action and
a human-readable reason. Rejection rules (per MVP spec):

  - session not RUNNING (no new trades while STOPPING/EXITING/ENDED)
  - BUY but already holding an open position
  - SELL but no open position
  - confidence below config.min_confidence
  - position size > config.position_size_pct
  - SL or TP not set (we enforce from config, never trust AI)
  - session loss already beyond config.max_session_loss_pct
  - paper balance too low to actually buy

Kill switch (env var) still overrides everything to HOLD.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.tenant import session_service

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.bot_config import BotConfig
    from app.models.position import Position
    from app.models.trading_session import TradingSession


def _kill_switch_active() -> bool:
    return os.getenv("KILL_SWITCH", "false").lower() == "true"


@dataclass
class RiskCheck:
    approved: bool
    action: str               # BUY | SELL | HOLD (post-risk verdict)
    reason: str               # rejection_reason if not approved, else short "ok" message
    confidence: float
    size_pct: float
    stop_loss_pct: float
    take_profit_pct: float


class RiskService:
    """Stateless validator. Kept as a class so it's easy to mock per-tenant."""

    def __init__(self, tenant_id: int) -> None:
        self.tenant_id = tenant_id

    def evaluate(
        self,
        decision: dict[str, Any],
        *,
        session: "TradingSession",
        config: "BotConfig",
        open_position: "Position | None",
        current_price: float,
        balance: float,
    ) -> RiskCheck:
        action = str(decision.get("action", "HOLD")).upper()
        confidence = float(decision.get("confidence", 0.0))
        # Size is no longer AI-controlled — config is the source of truth.
        size_pct = float(config.position_size_pct)
        # AI-proposed SL/TP, falling back to config defaults. We clamp to
        # the config cap so the AI can't suggest a 50% stop-loss.
        sl_pct = float(decision.get("stop_loss_pct") or 0) or float(config.stop_loss_pct)
        tp_pct = float(decision.get("take_profit_pct") or 0) or float(config.take_profit_pct)
        sl_pct = max(0.1, min(float(config.stop_loss_pct) * 2.0, sl_pct))
        tp_pct = max(0.1, min(float(config.take_profit_pct) * 3.0, tp_pct))

        base = RiskCheck(
            approved=False, action="HOLD", reason="",
            confidence=confidence, size_pct=0.0,
            stop_loss_pct=sl_pct, take_profit_pct=tp_pct,
        )

        if _kill_switch_active():
            return self._hold(base, "Kill switch active")

        if session.status != session_service.RUNNING:
            return self._hold(base, f"Session not RUNNING (status={session.status})")

        if action == "HOLD":
            base.reason = "HOLD"
            base.approved = True
            base.action = "HOLD"
            return base

        if action not in {"BUY", "SELL"}:
            return self._hold(base, f"Unknown action {action}")

        # Session loss guard — once tripped, no new BUYs. SELL-to-exit is
        # allowed so the user can still flatten.
        loss_pct = -session.session_pnl_pct if session.session_pnl_pct < 0 else 0.0
        if loss_pct >= config.max_session_loss_pct and action == "BUY":
            return self._hold(base, f"Session loss {loss_pct:.2f}% >= cap {config.max_session_loss_pct:.2f}%")

        if action == "BUY":
            if open_position is not None:
                return self._hold(base, f"Already holding {open_position.symbol}")
            if confidence < config.min_confidence:
                return self._hold(base, f"Confidence {confidence:.2f} < {config.min_confidence:.2f}")
            if size_pct <= 0:
                return self._hold(base, "Non-positive size")
            size_pct = min(size_pct, config.position_size_pct)
            if sl_pct <= 0 or tp_pct <= 0:
                return self._hold(base, "Missing SL or TP")
            if current_price <= 0:
                return self._hold(base, "No market price")
            required = balance * (size_pct / 100.0)
            if required <= 0 or balance < required:
                return self._hold(base, f"Balance {balance:.2f} too low for {size_pct:.1f}% buy")

            base.approved = True
            base.action = "BUY"
            base.size_pct = size_pct
            base.reason = "ok"
            return base

        # action == SELL
        if open_position is None:
            return self._hold(base, "No open position to sell")
        base.approved = True
        base.action = "SELL"
        base.size_pct = float(open_position.size_pct)
        base.reason = "ai_sell"
        return base

    @staticmethod
    def _hold(base: RiskCheck, reason: str) -> RiskCheck:
        base.approved = False
        base.action = "HOLD"
        base.size_pct = 0.0
        base.reason = reason
        logger.info("Risk rejected: %s", reason)
        return base
