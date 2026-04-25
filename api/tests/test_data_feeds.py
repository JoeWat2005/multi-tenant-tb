"""Tests for data_feeds — symbol normalisation + timeframe mapping."""
from __future__ import annotations

from app.services.shared import data_feeds


def test_to_alpaca_symbol_handles_btcusd_btcusdt_and_slash():
    assert data_feeds.to_alpaca_symbol("BTCUSDT") == "BTC/USD"
    assert data_feeds.to_alpaca_symbol("BTCUSD") == "BTC/USD"
    assert data_feeds.to_alpaca_symbol("BTC/USD") == "BTC/USD"
    assert data_feeds.to_alpaca_symbol("eth/usd") == "ETH/USD"


def test_to_alpaca_symbol_passthrough_when_unrecognised():
    # Unknown suffix → passthrough rather than mangling.
    assert data_feeds.to_alpaca_symbol("FOOEUR") == "FOOEUR"


def test_to_alpaca_timeframe_known():
    tf = data_feeds.to_alpaca_timeframe("30m")
    assert tf is not None
    assert data_feeds.to_alpaca_timeframe("1h") is not None
    assert data_feeds.to_alpaca_timeframe("4h") is not None


def test_to_alpaca_timeframe_unknown():
    assert data_feeds.to_alpaca_timeframe("7m") is None
    assert data_feeds.to_alpaca_timeframe("") is None
