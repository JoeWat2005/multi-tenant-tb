"""Tests for MarketContextService — focused on HOLD compression and the
end-to-end build path."""
from __future__ import annotations

from app.services.shared.market_context_service import _compress_holds


def _d(action: str, reason: str, *, conf: float = 0.4, approved: bool = True):
    return {
        "action": action, "confidence": conf, "reason": reason,
        "approved": approved, "created_at": "2026-04-25T12:00:00+00:00",
    }


def test_compresses_three_or_more_consecutive_holds_with_similar_reasons():
    decisions = [
        _d("BUY", "EMA cross"),
        _d("HOLD", "Price action shows tight range"),
        _d("HOLD", "Price action shows tight range"),
        _d("HOLD", "Price action shows tight range"),
    ]
    out = _compress_holds(decisions)
    # Original 4 → BUY + 1 summary HOLD
    assert len(out) == 2
    assert out[0]["action"] == "BUY"
    assert out[1]["action"] == "HOLD"
    assert "consecutive HOLDs" in out[1]["reason"]
    assert out[1]["compressed_count"] == 3


def test_does_not_compress_when_reasons_diverge():
    decisions = [
        _d("HOLD", "Price action shows tight range"),
        _d("HOLD", "EMA9 hovering near EMA21"),
        _d("HOLD", "Sentiment slightly negative"),
    ]
    out = _compress_holds(decisions)
    assert len(out) == 3  # not collapsed


def test_does_not_compress_short_runs():
    decisions = [
        _d("HOLD", "tight range"),
        _d("HOLD", "tight range"),
    ]
    assert _compress_holds(decisions) == decisions


def test_keeps_non_hold_tail_intact():
    decisions = [
        _d("HOLD", "tight range"),
        _d("HOLD", "tight range"),
        _d("HOLD", "tight range"),
        _d("BUY", "breakout"),
    ]
    out = _compress_holds(decisions)
    # Tail isn't HOLD → no collapse.
    assert out == decisions
