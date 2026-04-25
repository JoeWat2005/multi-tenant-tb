"""Claude implementation of DecisionProvider.

Consumes a `MarketContext` directly so the prompt sees raw data, not
hand-summarised "sentiment is bullish" labels. Returns a single Decision
parsed from a strict JSON shape:

    {"action": "BUY"|"SELL"|"HOLD", "confidence": 0.0,
     "reason": "...", "stopLossPct": 2, "takeProfitPct": 4}
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

import anthropic

from app.config import settings
from app.services.shared.decision_provider import Decision

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.shared.market_context_service import MarketContext

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


class ClaudeDecisionProvider:
    """Stateless Claude-backed DecisionProvider. The model is supplied per
    instance so different tenants could run different models."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model

    def name(self) -> str:
        return f"claude:{self.model}"

    async def decide(
        self, ctx: "MarketContext", *, min_confidence: float, max_position_pct: float,
    ) -> Decision:
        import asyncio
        return await asyncio.to_thread(
            self._decide_sync, ctx, min_confidence, max_position_pct,
        )

    # ── Sync path ──────────────────────────────────────────────────────────

    def _decide_sync(
        self, ctx: "MarketContext", min_confidence: float, max_position_pct: float,
    ) -> Decision:
        client = _get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self._system_prompt(min_confidence, max_position_pct, ctx),
            messages=[{"role": "user", "content": self._user_prompt(ctx)}],
        )

        text_blocks = [b.text for b in resp.content if b.type == "text"]
        if not text_blocks:
            return _default_hold(ctx.symbol, "Empty AI response")

        try:
            raw = _extract_json(text_blocks[0])
            parsed = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Failed to parse Claude JSON: %s", exc)
            return _default_hold(ctx.symbol, f"Parse error: {exc}")

        return self._validate(parsed, ctx, min_confidence, max_position_pct)

    # ── Prompts ────────────────────────────────────────────────────────────

    def _system_prompt(
        self, min_confidence: float, max_position_pct: float, ctx: "MarketContext",
    ) -> str:
        has_position = ctx.open_position is not None
        directional = "SELL or HOLD" if has_position else "BUY or HOLD"
        return f"""You are a disciplined crypto trading analyst for {ctx.symbol} on the {ctx.interval} timeframe.

You will receive raw market data, news headlines, technical indicators, sentiment numbers, and recent decision/trade history. Your job is to weigh that evidence and output ONE decision as strict JSON.

Rules:
- Return ONLY a single JSON object — no markdown, no prose, no JSON array.
- Allowed actions: BUY, SELL, HOLD.
- Action must be {directional} given the current position state.
- Only BUY/SELL when confidence >= {min_confidence:.2f}. Otherwise HOLD with confidence reflecting your uncertainty.
- Be willing to act when the evidence supports it — HOLD is not free; missing trades is a real cost.
- stopLossPct and takeProfitPct are percentages (e.g. 2 means 2%, NOT 0.02). They guide where the position's exits will sit if you BUY. Suggest sensible values for the {ctx.interval} timeframe (typical range 1.5–5% SL, 3–10% TP). Cap position influence at {max_position_pct:.0f}% of balance.
- reason is a single sentence (<=200 chars) explaining the *primary* driver — cite specific evidence from the inputs (e.g. "EMA9 crossed above EMA21 with rising volume and 2 bullish news headlines").

Output schema (strict):
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0-1.0>,
  "reason": "<one sentence>",
  "stopLossPct": <number>,
  "takeProfitPct": <number>
}}"""

    def _user_prompt(self, ctx: "MarketContext") -> str:
        lines: list[str] = []

        # ── State ────────────────────────────────────────────────────────
        lines.append("=== POSITION & BALANCE ===")
        if ctx.open_position is None:
            lines.append("Currently FLAT (no open position).")
        else:
            p = ctx.open_position
            lines.append(
                f"OPEN: {p['symbol']} qty={p['quantity']:.6f} entry=${p['entry_price']:,.4f} "
                f"size={p['size_pct']:.1f}% SL=${p.get('stop_loss_price') or 0:,.4f} "
                f"TP=${p.get('take_profit_price') or 0:,.4f} unrealised=${p['unrealised_pnl']:,.2f}"
            )
        lines.append(
            f"Balance ${ctx.current_balance:,.2f} | "
            f"Session P&L ${ctx.session_pnl:+,.2f} ({ctx.session_pnl_pct:+.2f}%)"
        )
        lines.append("")

        # ── Technical indicators ─────────────────────────────────────────
        if ctx.technicals is not None:
            t = ctx.technicals
            lines.append("=== TECHNICAL INDICATORS ===")
            lines.append(
                f"EMA9={_fmt(t.ema_9)} EMA21={_fmt(t.ema_21)} "
                f"RSI14={_fmt(t.rsi_14)} MACD={_fmt(t.macd)} signal={_fmt(t.macd_signal)}"
            )
            lines.append(
                f"price_change_pct={_fmt(t.price_change_pct)}% "
                f"volume_change_pct={_fmt(t.volume_change_pct)}% "
                f"trend={t.trend_label}"
            )
            lines.append("")

        # ── Recent OHLCV ─────────────────────────────────────────────────
        if ctx.candles:
            lines.append("=== RECENT OHLCV (oldest → newest) ===")
            for c in ctx.candles[-12:]:  # last 12 keeps the prompt tight
                lines.append(
                    f"  {c['open_time']}  O={c['open']:.4f}  H={c['high']:.4f}  "
                    f"L={c['low']:.4f}  C={c['close']:.4f}  V={c['volume']:.2f}"
                )
            lines.append("")

        # ── Sentiment ────────────────────────────────────────────────────
        lines.append("=== SENTIMENT ===")
        if ctx.weighted_sentiment_score is None:
            lines.append("(no sentiment data in lookback window)")
        else:
            lines.append(
                f"weighted_score={ctx.weighted_sentiment_score:+.3f} "
                f"(scale -1..+1, blended across sources with recency + source-quality weights)"
            )
            for src, info in ctx.source_breakdown.items():
                lines.append(
                    f"  {src}: score={info['score']:+.3f} label={info['label']} "
                    f"age={info['age_min']:.0f}min weight={info['weight']:.2f} "
                    f"articles={info['article_count']}"
                )
        if ctx.fear_greed is not None:
            lines.append(
                f"  fear_greed: value={ctx.fear_greed.get('value')} "
                f"({ctx.fear_greed.get('classification')})"
            )
        lines.append("")

        # ── News ─────────────────────────────────────────────────────────
        if ctx.news_articles:
            lines.append("=== TOP NEWS HEADLINES ===")
            for art in ctx.news_articles:
                summary = art.get("summary") or ""
                if summary:
                    summary = " — " + summary[:160]
                lines.append(
                    f"  [{art.get('source')}] {art['headline']}{summary}  "
                    f"(score {art['score']:+.2f})"
                )
            lines.append("")

        # ── Memory ───────────────────────────────────────────────────────
        if ctx.previous_decisions:
            lines.append("=== PREVIOUS DECISIONS (oldest → newest) ===")
            for d in ctx.previous_decisions:
                approved = "OK" if d["approved"] else "REJECTED"
                lines.append(
                    f"  {d['action']} conf={d['confidence']:.2f} [{approved}] — \"{d['reason']}\""
                )
            lines.append("")

        if ctx.recent_trades:
            lines.append("=== RECENT TRADES ===")
            for t in ctx.recent_trades:
                lines.append(
                    f"  {t['side']} qty={t['quantity']:.6f} @ ${t['price']:,.4f} "
                    f"fee=${t['fee']:.4f} at {t.get('filled_at')}"
                )
            lines.append("")

        lines.append("Return a single JSON object exactly matching the schema. No prose.")
        return "\n".join(lines)

    # ── Validation ─────────────────────────────────────────────────────────

    def _validate(
        self, raw: object, ctx: "MarketContext",
        min_confidence: float, max_position_pct: float,
    ) -> Decision:
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not isinstance(raw, dict):
            return _default_hold(ctx.symbol, "Response not a JSON object")

        action = str(raw.get("action", "HOLD")).upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            action = "HOLD"

        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        try:
            sl = float(raw.get("stopLossPct", 0.0) or 0.0)
        except (TypeError, ValueError):
            sl = 0.0
        try:
            tp = float(raw.get("takeProfitPct", 0.0) or 0.0)
        except (TypeError, ValueError):
            tp = 0.0
        # Guard against silly values
        sl = max(0.1, min(20.0, sl)) if action == "BUY" else sl
        tp = max(0.1, min(40.0, tp)) if action == "BUY" else tp

        reason = str(raw.get("reason") or "").strip() or "(no reason)"

        # Position-state and confidence gates (same rules the system prompt
        # describes — enforce here too in case the model misbehaves).
        has_position = ctx.open_position is not None
        if action == "BUY" and has_position:
            action = "HOLD"
            reason = "Cannot BUY while a position is open — forced HOLD."
        elif action == "SELL" and not has_position:
            action = "HOLD"
            reason = "Cannot SELL without an open position — forced HOLD."
        if confidence < min_confidence and action != "HOLD":
            action = "HOLD"
            reason = f"Confidence {confidence:.2f} below threshold {min_confidence:.2f} — forced HOLD."

        # size_pct is no longer AI-controlled; risk + config own it.
        size_pct = float(max_position_pct) if action == "BUY" else 0.0

        return Decision(
            symbol=ctx.symbol,
            action=action,
            confidence=round(confidence, 4),
            size_pct=size_pct,
            reasoning=reason,
            stop_loss_pct=round(sl, 4),
            take_profit_pct=round(tp, 4),
        )


# ── Helpers ───────────────────────────────────────────────────────────────


def _default_hold(symbol: str, reason: str) -> Decision:
    return Decision(
        symbol=symbol, action="HOLD", confidence=0.0, size_pct=0,
        reasoning=reason, stop_loss_pct=0.0, take_profit_pct=0.0,
    )


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.4f}" if abs(v) < 1000 else f"{v:.2f}"


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
    # Object first, then array fallback
    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        return obj.group(0)
    arr = re.search(r"\[.*\]", text, re.DOTALL)
    return arr.group(0) if arr else text
