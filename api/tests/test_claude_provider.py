"""Tests for ClaudeDecisionProvider — JSON parsing + validation rules.
The actual API call is mocked; these are pure-logic tests on the parser."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.shared.claude_provider import ClaudeDecisionProvider, _extract_json
from app.services.shared.market_context_service import MarketContext


def _ctx(open_position=None) -> MarketContext:
    return MarketContext(
        symbol="BTC/USD", interval="30m",
        weighted_sentiment_score=0.1,
        open_position=open_position,
        candles=[{"open_time": "2026-04-25T12:00:00+00:00", "open": 100,
                  "high": 101, "low": 99, "close": 100, "volume": 1.0}],
    )


def test_extract_json_object():
    text = '```json\n{"action": "BUY", "confidence": 0.9}\n```'
    assert _extract_json(text) == '{"action": "BUY", "confidence": 0.9}'


def test_extract_json_handles_leading_prose():
    text = 'Here is my decision: {"action": "HOLD", "confidence": 0.4}'
    assert _extract_json(text) == '{"action": "HOLD", "confidence": 0.4}'


def test_validate_well_formed_buy():
    p = ClaudeDecisionProvider()
    raw = {"action": "BUY", "confidence": 0.8, "reason": "test",
           "stopLossPct": 2, "takeProfitPct": 5}
    d = p._validate(raw, _ctx(), min_confidence=0.65, max_position_pct=10)
    assert d.action == "BUY"
    assert d.confidence == 0.8
    assert d.stop_loss_pct == 2.0
    assert d.take_profit_pct == 5.0


def test_validate_clamps_confidence_above_one():
    p = ClaudeDecisionProvider()
    raw = {"action": "BUY", "confidence": 5.0, "reason": "x",
           "stopLossPct": 2, "takeProfitPct": 4}
    d = p._validate(raw, _ctx(), min_confidence=0.65, max_position_pct=10)
    assert d.confidence == 1.0


def test_validate_forces_hold_when_below_threshold():
    p = ClaudeDecisionProvider()
    raw = {"action": "BUY", "confidence": 0.4, "reason": "x",
           "stopLossPct": 2, "takeProfitPct": 4}
    d = p._validate(raw, _ctx(), min_confidence=0.65, max_position_pct=10)
    assert d.action == "HOLD"
    assert "below threshold" in d.reasoning


def test_validate_blocks_buy_when_position_open():
    p = ClaudeDecisionProvider()
    raw = {"action": "BUY", "confidence": 0.9, "reason": "x",
           "stopLossPct": 2, "takeProfitPct": 4}
    ctx = _ctx(open_position={"symbol": "BTC/USD", "entry_price": 100,
                              "quantity": 1, "size_pct": 10,
                              "stop_loss_price": 98, "take_profit_price": 104,
                              "unrealised_pnl": 0, "opened_at": None})
    d = p._validate(raw, ctx, min_confidence=0.65, max_position_pct=10)
    assert d.action == "HOLD"
    assert "Cannot BUY" in d.reasoning


def test_validate_blocks_sell_when_no_position():
    p = ClaudeDecisionProvider()
    raw = {"action": "SELL", "confidence": 0.9, "reason": "x",
           "stopLossPct": 0, "takeProfitPct": 0}
    d = p._validate(raw, _ctx(), min_confidence=0.65, max_position_pct=10)
    assert d.action == "HOLD"
    assert "Cannot SELL" in d.reasoning


def test_validate_handles_array_response():
    """If Claude returns a JSON array (as the old prompt asked for), unwrap it."""
    p = ClaudeDecisionProvider()
    raw = [{"action": "HOLD", "confidence": 0.3, "reason": "x",
            "stopLossPct": 0, "takeProfitPct": 0}]
    d = p._validate(raw, _ctx(), min_confidence=0.65, max_position_pct=10)
    assert d.action == "HOLD"


def test_validate_unknown_action_falls_back_to_hold():
    p = ClaudeDecisionProvider()
    raw = {"action": "TEAR-IT-DOWN", "confidence": 0.9, "reason": "x",
           "stopLossPct": 2, "takeProfitPct": 4}
    d = p._validate(raw, _ctx(), min_confidence=0.65, max_position_pct=10)
    assert d.action == "HOLD"


def test_decide_sync_handles_empty_response(monkeypatch):
    p = ClaudeDecisionProvider()
    fake_resp = MagicMock()
    fake_resp.content = []  # no text blocks
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp
    monkeypatch.setattr(
        "app.services.shared.claude_provider._get_client", lambda: fake_client,
    )
    d = p._decide_sync(_ctx(), 0.65, 10)
    assert d.action == "HOLD"
    assert "Empty AI response" in d.reasoning


def test_decide_sync_handles_garbage_json(monkeypatch):
    p = ClaudeDecisionProvider()
    block = MagicMock(); block.type = "text"; block.text = "lol not json"
    fake_resp = MagicMock(); fake_resp.content = [block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp
    monkeypatch.setattr(
        "app.services.shared.claude_provider._get_client", lambda: fake_client,
    )
    d = p._decide_sync(_ctx(), 0.65, 10)
    assert d.action == "HOLD"
    assert "Parse error" in d.reasoning


def test_decide_sync_happy_path(monkeypatch):
    p = ClaudeDecisionProvider()
    block = MagicMock()
    block.type = "text"
    block.text = '{"action": "BUY", "confidence": 0.85, "reason": "EMA cross", ' \
                 '"stopLossPct": 2, "takeProfitPct": 5}'
    fake_resp = MagicMock(); fake_resp.content = [block]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp
    monkeypatch.setattr(
        "app.services.shared.claude_provider._get_client", lambda: fake_client,
    )
    d = p._decide_sync(_ctx(), 0.65, 10)
    assert d.action == "BUY"
    assert d.confidence == 0.85
    assert d.stop_loss_pct == 2.0
    assert d.take_profit_pct == 5.0
    assert "EMA cross" in d.reasoning
