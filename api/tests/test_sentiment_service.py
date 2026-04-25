"""Tests for the upgraded sentiment service: weighted_score with mixed
sources/recencies, headline normalization, source_breakdown, and dedup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.sentiment_snapshot import SentimentSnapshot
from app.services.shared import sentiment_service


def _add(db, *, source: str, symbol: str, score: float, age_minutes: float = 0):
    snap = SentimentSnapshot(
        symbol=symbol,
        source=source,
        score=score,
        label=sentiment_service.label_for(score),
        source_weight=sentiment_service.SOURCE_WEIGHTS.get(source, 1.0),
        article_count=1,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )
    db.add(snap)
    db.commit()
    return snap


def test_weighted_score_blends_sources_with_their_weights(db):
    # alpaca_news (weight 1.3) at +0.5, fresh
    # rss (weight 0.7) at -1.0, fresh
    # The higher-weighted source should dominate.
    _add(db, source="alpaca_news", symbol="BTC/USD", score=0.5)
    _add(db, source="rss", symbol="BTC/USD", score=-1.0)

    score = sentiment_service.weighted_score(db, "BTC/USD")
    assert score is not None
    # (0.5*1.3 + -1.0*0.7) / (1.3 + 0.7) = (0.65 - 0.70) / 2.0 = -0.025
    assert abs(score - (-0.025)) < 1e-3


def test_recency_decay_discounts_old_snapshots(db):
    # Same symbol, same score, different ages — older one should contribute less.
    _add(db, source="price_action", symbol="ETH/USD", score=1.0, age_minutes=0)
    _add(db, source="price_action", symbol="ETH/USD", score=-1.0, age_minutes=10 * 60)  # 10h old

    score = sentiment_service.weighted_score(db, "ETH/USD")
    assert score is not None
    # 10h with 90min half-life → recency factor ~exp(-600/90) ≈ 0.0012, basically nil.
    assert score > 0.95


def test_global_snapshots_blend_into_per_symbol(db):
    _add(db, source="fear_greed", symbol="GLOBAL", score=-0.5)
    _add(db, source="alpaca_news", symbol="BTC/USD", score=0.0)

    score = sentiment_service.weighted_score(db, "BTC/USD")
    assert score is not None
    assert score < 0  # F&G drags it negative


def test_returns_none_when_no_snapshots(db):
    assert sentiment_service.weighted_score(db, "BTC/USD") is None


def test_normalize_headline_strips_punctuation_and_case(db):
    a = sentiment_service.normalize_headline("Bitcoin Surges Past $80k!!! ")
    b = sentiment_service.normalize_headline("bitcoin surges past 80k")
    # Punctuation differs but words are the same.
    assert a == "bitcoin surges past 80k"
    assert b == "bitcoin surges past 80k"


def test_source_breakdown_returns_latest_per_source(db):
    _add(db, source="alpaca_news", symbol="BTC/USD", score=-0.2, age_minutes=120)
    fresh = _add(db, source="alpaca_news", symbol="BTC/USD", score=0.3, age_minutes=5)
    _add(db, source="rss", symbol="BTC/USD", score=0.1, age_minutes=30)

    bd = sentiment_service.source_breakdown(db, "BTC/USD")
    assert "alpaca_news" in bd
    assert "rss" in bd
    # Most recent alpaca_news wins.
    assert abs(bd["alpaca_news"]["score"] - 0.3) < 1e-9
    assert bd["alpaca_news"]["weight"] == 1.3
    assert bd["rss"]["weight"] == 0.7
    assert fresh.id  # ensure the row was actually persisted
