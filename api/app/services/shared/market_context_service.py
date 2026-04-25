"""Build the full per-cycle context for an AI decision and persist a
replay-able snapshot to `market_context_snapshots`.

This is the single entry point that `TradingCycleService` calls to
assemble everything Claude needs:
  - weighted sentiment + per-source breakdown
  - top N raw news headlines (already deduped across sources)
  - raw F&G value + classification
  - last 20 closed candles (OHLCV)
  - technical indicators
  - current open position (if any)
  - session P&L
  - last 5 useful previous decisions (with HOLD compression)
  - last 3 completed trades

Decision ID is filled in *after* the BotDecision row is committed via
`attach_decision_id()` so the persisted snapshot links back to the verdict.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import desc

from app.models.bot_decision import BotDecision
from app.models.candle import Candle
from app.models.market_context_snapshot import MarketContextSnapshot
from app.models.position import Position
from app.models.technical_snapshot import TechnicalSnapshot
from app.models.trade import Trade
from app.services.shared import sentiment_service, technical_service
from app.services.shared.technical_service import TechnicalReading

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.bot_config import BotConfig
    from app.models.trading_session import TradingSession


PREV_DECISION_LIMIT = 5
RECENT_TRADE_LIMIT = 3
NEWS_HEADLINE_LIMIT = 8
CANDLE_LIMIT = 20


@dataclass
class MarketContext:
    symbol: str
    interval: str

    # Sentiment block
    weighted_sentiment_score: float | None = None
    source_breakdown: dict = field(default_factory=dict)
    news_articles: list[dict] = field(default_factory=list)
    fear_greed: dict | None = None

    # Market data block
    candles: list[dict] = field(default_factory=list)
    technicals: TechnicalReading | None = None

    # Per-tenant state block
    open_position: dict | None = None
    session_pnl: float = 0.0
    session_pnl_pct: float = 0.0
    current_balance: float = 0.0

    # Memory block
    previous_decisions: list[dict] = field(default_factory=list)
    recent_trades: list[dict] = field(default_factory=list)

    # The persisted snapshot row, for later attach_decision_id().
    _snapshot_id: int | None = None


class MarketContextService:
    """Stateless builder. Caller passes db + session + config; we package
    the context and write a MarketContextSnapshot + TechnicalSnapshot."""

    @staticmethod
    def build(
        db: "Session",
        *,
        session: "TradingSession",
        config: "BotConfig",
    ) -> MarketContext:
        symbol = (config.symbol or "BTC/USD").upper()
        interval = config.interval or "30m"

        ctx = MarketContext(symbol=symbol, interval=interval)

        # ── Sentiment ────────────────────────────────────────────────────
        if config.use_sentiment:
            ctx.weighted_sentiment_score = sentiment_service.weighted_score(db, symbol)
            ctx.source_breakdown = sentiment_service.source_breakdown(db, symbol)
            ctx.news_articles = sentiment_service.latest_news_articles(
                db, symbol, limit=NEWS_HEADLINE_LIMIT,
            )
            ctx.fear_greed = sentiment_service.latest_fear_greed(db)

        # ── Market data ──────────────────────────────────────────────────
        candle_rows = (
            db.query(Candle)
            .filter(
                Candle.symbol == symbol,
                Candle.interval == interval,
                Candle.is_closed == True,  # noqa: E712
            )
            .order_by(desc(Candle.open_time))
            .limit(CANDLE_LIMIT)
            .all()
        )
        ctx.candles = [
            {
                "open_time": c.open_time.isoformat() if c.open_time else None,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in reversed(candle_rows)
        ]
        ctx.technicals = technical_service.compute(db, symbol, interval=interval)

        # ── Position + P&L ────────────────────────────────────────────────
        ctx.current_balance = float(session.current_balance or 0)
        ctx.session_pnl = float(session.session_pnl or 0)
        ctx.session_pnl_pct = float(session.session_pnl_pct or 0)
        open_pos = (
            db.query(Position)
            .filter(Position.session_id == session.id, Position.status == "open")
            .order_by(desc(Position.id))
            .first()
        )
        if open_pos is not None:
            ctx.open_position = {
                "symbol": open_pos.symbol,
                "entry_price": float(open_pos.entry_price),
                "quantity": float(open_pos.quantity),
                "size_pct": float(open_pos.size_pct),
                "stop_loss_price": (
                    float(open_pos.stop_loss_price) if open_pos.stop_loss_price else None
                ),
                "take_profit_price": (
                    float(open_pos.take_profit_price) if open_pos.take_profit_price else None
                ),
                "unrealised_pnl": float(open_pos.unrealised_pnl or 0),
                "opened_at": open_pos.opened_at.isoformat() if open_pos.opened_at else None,
            }

        # ── Memory: previous decisions (with HOLD compression) ───────────
        prev_rows = (
            db.query(BotDecision)
            .filter(
                BotDecision.tenant_id == session.tenant_id,
                BotDecision.session_id == session.id,
            )
            .order_by(desc(BotDecision.created_at))
            .limit(PREV_DECISION_LIMIT)
            .all()
        )
        ctx.previous_decisions = _compress_holds([
            {
                "action": r.action,
                "confidence": float(r.confidence or 0),
                "reason": r.reason or "",
                "approved": bool(r.approved),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reversed(prev_rows)
        ])

        # ── Recent completed trades ──────────────────────────────────────
        trade_rows = (
            db.query(Trade)
            .filter(Trade.session_id == session.id)
            .order_by(desc(Trade.created_at))
            .limit(RECENT_TRADE_LIMIT)
            .all()
        )
        ctx.recent_trades = [
            {
                "side": t.side,
                "price": float(t.price),
                "quantity": float(t.quantity),
                "fee": float(t.fee or 0),
                "filled_at": t.filled_at.isoformat() if t.filled_at else None,
            }
            for t in reversed(trade_rows)
        ]

        # ── Persist ──────────────────────────────────────────────────────
        ctx._snapshot_id = _persist(db, ctx, session=session)
        return ctx

    @staticmethod
    def attach_decision_id(db: "Session", ctx: MarketContext, decision_id: int) -> None:
        """Link the just-written BotDecision back to its context snapshot."""
        if ctx._snapshot_id is None:
            return
        snap = db.query(MarketContextSnapshot).filter(
            MarketContextSnapshot.id == ctx._snapshot_id
        ).first()
        if snap is not None:
            snap.decision_id = decision_id
            db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────


def _compress_holds(decisions: list[dict]) -> list[dict]:
    """If the last 3+ decisions are all HOLD with similar reasons, collapse
    them into one summary line. Keeps non-HOLD decisions intact."""
    if len(decisions) < 3:
        return decisions

    # Walk from the most recent backwards. Find the longest tail of HOLDs.
    tail_holds: list[dict] = []
    for d in reversed(decisions):
        if d["action"] == "HOLD":
            tail_holds.append(d)
        else:
            break
    if len(tail_holds) < 3:
        return decisions

    # All same first-six-words? Treat as similar.
    def _key(d: dict) -> str:
        return " ".join((d.get("reason") or "").lower().split()[:6])

    keys = {_key(d) for d in tail_holds}
    if len(keys) > 1:
        return decisions  # reasons too varied to collapse

    head = decisions[: -len(tail_holds)]
    summary = {
        "action": "HOLD",
        "confidence": round(
            sum(d["confidence"] for d in tail_holds) / len(tail_holds), 3
        ),
        "reason": (
            f"[{len(tail_holds)} consecutive HOLDs with similar reasoning] "
            + (tail_holds[-1].get("reason") or "")[:200]
        ),
        "approved": all(d["approved"] for d in tail_holds),
        "created_at": tail_holds[0]["created_at"],  # oldest of the run
        "compressed_count": len(tail_holds),
    }
    return head + [summary]


def _persist(
    db: "Session", ctx: MarketContext, *, session: "TradingSession",
) -> int | None:
    """Write MarketContextSnapshot + TechnicalSnapshot rows. Returns the
    market-context row id so the caller can attach a decision_id later."""
    try:
        sentiment_block = {
            "weighted_score": ctx.weighted_sentiment_score,
            "sources": ctx.source_breakdown,
        }
        memory_block = {
            "previous_decisions": ctx.previous_decisions,
            "recent_trades": ctx.recent_trades,
            "open_position": ctx.open_position,
            "session_pnl": ctx.session_pnl,
            "session_pnl_pct": ctx.session_pnl_pct,
            "current_balance": ctx.current_balance,
        }
        tech_dict = (
            {
                "ema_9": ctx.technicals.ema_9,
                "ema_21": ctx.technicals.ema_21,
                "rsi_14": ctx.technicals.rsi_14,
                "macd": ctx.technicals.macd,
                "macd_signal": ctx.technicals.macd_signal,
                "volume_change_pct": ctx.technicals.volume_change_pct,
                "price_change_pct": ctx.technicals.price_change_pct,
                "trend_label": ctx.technicals.trend_label,
                "samples": ctx.technicals.samples,
            }
            if ctx.technicals is not None
            else {}
        )

        snap = MarketContextSnapshot(
            session_id=session.id,
            symbol=ctx.symbol,
            interval=ctx.interval,
            weighted_sentiment_score=ctx.weighted_sentiment_score,
            source_breakdown=json.dumps(sentiment_block, default=str),
            raw_news_context=json.dumps(ctx.news_articles, default=str),
            fear_greed_context=json.dumps(ctx.fear_greed, default=str) if ctx.fear_greed else None,
            technical_context=json.dumps(tech_dict, default=str),
            decision_memory_context=json.dumps(memory_block, default=str),
            created_at=datetime.now(timezone.utc),
        )
        db.add(snap)

        if ctx.technicals is not None and ctx.technicals.samples > 0:
            db.add(TechnicalSnapshot(
                session_id=session.id,
                symbol=ctx.symbol,
                interval=ctx.interval,
                ema_9=ctx.technicals.ema_9,
                ema_21=ctx.technicals.ema_21,
                rsi_14=ctx.technicals.rsi_14,
                macd=ctx.technicals.macd,
                macd_signal=ctx.technicals.macd_signal,
                volume_change_pct=ctx.technicals.volume_change_pct,
                price_change_pct=ctx.technicals.price_change_pct,
                trend_label=ctx.technicals.trend_label,
                created_at=datetime.now(timezone.utc),
            ))

        db.commit()
        return snap.id
    except Exception:
        logger.exception("Failed to persist MarketContextSnapshot")
        db.rollback()
        return None
