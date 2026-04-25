"""Tests for the synchronous data seed run by POST /api/bot/start before
the session is flipped to RUNNING."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.api.routes import bot_control


async def test_seed_refreshes_candles_then_all_four_sentiment_sources(
    monkeypatch, session_factory,
):
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)

    candle_refresh = MagicMock(return_value=0)
    fg = MagicMock(return_value=None)
    news = MagicMock(return_value=None)
    rss = MagicMock(return_value=None)
    price = MagicMock(return_value=None)

    monkeypatch.setattr(
        "app.services.shared.market_data_service.refresh", candle_refresh,
    )
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_fear_greed", fg)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_alpaca_news", news)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_rss", rss)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_price_action", price)

    await bot_control._seed_initial_data("BTC/USD", "30m")

    # Candles refreshed first (so price_action has data when it runs).
    assert candle_refresh.call_count == 1
    assert candle_refresh.call_args.args[1] == "BTC/USD"
    assert candle_refresh.call_args.args[2] == "30m"

    # All four sentiment sources fired.
    assert fg.call_count == 1
    assert news.call_count == 1
    assert rss.call_count == 1
    assert price.call_count == 1
    # Symbol routed correctly to per-symbol refreshers.
    assert news.call_args.args[1] == "BTC/USD"
    assert rss.call_args.args[1] == "BTC/USD"
    assert price.call_args.args[1] == "BTC/USD"


async def test_seed_does_not_raise_when_candle_refresh_fails(monkeypatch, session_factory):
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)

    def boom(*a, **kw):
        raise RuntimeError("alpaca down")

    monkeypatch.setattr("app.services.shared.market_data_service.refresh", boom)
    # Sentiment refreshers stay reachable; they should still run.
    fg = MagicMock(return_value=None)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_fear_greed", fg)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_alpaca_news", MagicMock())
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_rss", MagicMock())
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_price_action", MagicMock())

    await bot_control._seed_initial_data("BTC/USD", "30m")

    # Sentiment refresh still happened despite the candle failure.
    assert fg.call_count == 1


async def test_seed_does_not_raise_when_every_source_blows_up(monkeypatch, session_factory):
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)

    def boom(*a, **kw):
        raise RuntimeError("upstream down")

    monkeypatch.setattr("app.services.shared.market_data_service.refresh", boom)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_fear_greed", boom)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_alpaca_news", boom)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_rss", boom)
    monkeypatch.setattr("app.services.shared.sentiment_service.refresh_price_action", boom)

    # Should not raise — start endpoint must remain callable even if every
    # upstream is unreachable.
    await bot_control._seed_initial_data("BTC/USD", "30m")
