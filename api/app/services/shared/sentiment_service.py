"""Sentiment pipeline — fetch raw snapshots + compute a weighted score that
combines source quality with recency.

Sources (and their multiplicative weight in `weighted_score`):
  - alpaca_news   1.3   curated per-symbol news (highest signal)
  - price_action  1.2   momentum + range from local candles
  - fear_greed    0.8   global F&G index
  - rss           0.7   generic crypto-news feeds (lowest signal-to-noise)

Each source writes a -1..+1 normalised `score` into `sentiment_snapshots`.
`weighted_score(symbol)` blends them with:

    weight_i = exp(-age_min / half_life) * source_weight
    score    = sum(score_i * weight_i) / sum(weight_i)

defaulting to half_life=90min, lookback=24h.

Dedup:
  - Articles: by URL when present, else by normalised headline (lowercase,
    no punctuation, trimmed).
  - Snapshots: skip writing if `payload_hash` matches the previous row from
    the same (source, symbol). Also computes a `normalized_hash` of the
    deduped content set so reorderings don't create false-positive writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import string
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import desc

from app.config import settings
from app.models.sentiment_snapshot import SentimentSnapshot

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.services.orchestration.tenant_registry import TenantRegistry


# ── Config ────────────────────────────────────────────────────────────────

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

DEFAULT_RSS_FEEDS = [
    "https://cryptopotato.com/feed/",
    "https://news.bitcoin.com/feed/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

SYMBOL_KEYWORDS = {
    "BTC/USD": ["bitcoin", "btc"],
    "ETH/USD": ["ethereum", "eth"],
    "SOL/USD": ["solana", "sol"],
    "XRP/USD": ["xrp", "ripple"],
    "DOGE/USD": ["dogecoin", "doge"],
    "AVAX/USD": ["avalanche", "avax"],
    "LINK/USD": ["chainlink", "link"],
    "LTC/USD": ["litecoin", "ltc"],
    "BCH/USD": ["bitcoin cash", "bch"],
    "UNI/USD": ["uniswap", "uni"],
    "AAVE/USD": ["aave"],
}

# Crypto-specific lexicon additions for VADER. Out-of-the-box VADER is
# tuned for general English; adding these dramatically improves headline
# scoring (a "bitcoin surges" or "ETH dumps" headline should not score 0).
CRYPTO_LEXICON = {
    "pump": 2.5, "pumps": 2.5, "pumped": 2.0,
    "dump": -3.0, "dumps": -3.0, "dumped": -2.5,
    "moon": 3.0, "mooning": 3.0, "moonshot": 2.5,
    "bullish": 2.5, "bearish": -2.5,
    "rally": 2.0, "rallies": 2.0, "surges": 2.5, "surged": 2.5, "surge": 2.5,
    "crash": -3.0, "crashes": -3.0, "crashed": -2.5,
    "plummet": -2.5, "plummets": -2.5, "plunge": -2.5, "plunges": -2.5,
    "ath": 2.5, "all-time high": 2.5, "new high": 2.0, "new low": -2.0,
    "rekt": -2.5, "liquidated": -2.0, "liquidation": -1.5,
    "hodl": 0.5, "hodling": 0.5,
    "fud": -2.0, "fomo": -0.5,
    "breakout": 1.5, "breakdown": -1.5,
    "rugpull": -3.0, "scam": -2.5, "hack": -2.5, "hacked": -2.5, "exploit": -2.0,
    "approval": 1.5, "approved": 1.5,
    "sec": -0.5, "lawsuit": -2.0, "banned": -2.5, "ban": -2.0,
    "etf": 1.5, "institutional": 1.0, "adoption": 1.5,
    "whale": 0.0, "whales": 0.0,
}

# Per-source multiplier in weighted_score. Higher = more influence.
SOURCE_WEIGHTS: dict[str, float] = {
    "alpaca_news": 1.3,
    "price_action": 1.2,
    "fear_greed": 0.8,
    "rss": 0.7,
}
DEFAULT_SOURCE_WEIGHT = 1.0

# Per-source refresh cadences. The outer task ticks every FAST_TICK_SECONDS
# but each source self-skips if its own MIN_GAP hasn't elapsed since its
# last successful write.
FAST_TICK_SECONDS = 60 * 15
MIN_GAP = {
    "fear_greed": 60 * 60,          # F&G updates once per day; hourly is plenty
    "rss": 60 * 15,                 # feeds rotate slowly
    "alpaca_news": 60 * 15,         # match the bot's minimum trading interval
    "price_action": 60 * 15,
}


# ── Helpers ───────────────────────────────────────────────────────────────


def label_for(score: float) -> str:
    if score >= 0.25:
        return "bullish"
    if score <= -0.25:
        return "bearish"
    return "neutral"


_PUNCT_RE = re.compile(rf"[{re.escape(string.punctuation)}]")


def normalize_headline(text: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace. Used to dedup
    articles whose URLs differ but whose headlines are effectively the same."""
    if not text:
        return ""
    cleaned = _PUNCT_RE.sub(" ", text.lower())
    return " ".join(cleaned.split())


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _normalized_hash(items: list[str]) -> str:
    """Order-insensitive hash of a deduplicated set of normalised strings."""
    cleaned = sorted({normalize_headline(s) for s in items if s})
    return hashlib.sha256("\n".join(cleaned).encode()).hexdigest()


def _last_snapshot(
    db: "Session", *, source: str, symbol: str,
) -> SentimentSnapshot | None:
    return (
        db.query(SentimentSnapshot)
        .filter(
            SentimentSnapshot.source == source,
            SentimentSnapshot.symbol == symbol,
        )
        .order_by(desc(SentimentSnapshot.created_at))
        .first()
    )


def _store(
    db: "Session",
    *,
    symbol: str,
    source: str,
    score: float,
    payload: dict,
    article_count: int = 0,
    normalized_items: list[str] | None = None,
    metadata: dict | None = None,
) -> SentimentSnapshot | None:
    """Dedup-and-store. Skips the write if either the payload hash OR the
    normalised-content hash matches the previous snapshot for this source."""
    new_payload_hash = _payload_hash(payload)
    new_norm_hash = _normalized_hash(normalized_items or []) if normalized_items else None

    prev = _last_snapshot(db, source=source, symbol=symbol)
    if prev is not None:
        if prev.payload_hash and prev.payload_hash == new_payload_hash:
            return None
        if new_norm_hash and prev.normalized_hash and prev.normalized_hash == new_norm_hash:
            return None

    snap = SentimentSnapshot(
        symbol=symbol,
        score=score,
        label=label_for(score),
        source=source,
        source_weight=SOURCE_WEIGHTS.get(source, DEFAULT_SOURCE_WEIGHT),
        payload_hash=new_payload_hash,
        normalized_hash=new_norm_hash,
        article_count=article_count,
        raw_payload=json.dumps(payload),
        snapshot_metadata=json.dumps(metadata) if metadata else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(snap)
    db.commit()
    return snap


_analyzer = None


def _vader():
    """Lazy-init the VADER analyzer with the crypto lexicon applied."""
    global _analyzer
    if _analyzer is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        a = SentimentIntensityAnalyzer()
        a.lexicon.update(CRYPTO_LEXICON)
        _analyzer = a
    return _analyzer


# ── Sources ───────────────────────────────────────────────────────────────


def refresh_fear_greed(db: "Session") -> SentimentSnapshot | None:
    """Fetch the global F&G value. Dedups against the previous snapshot —
    since F&G only moves once per day, most ticks are no-ops."""
    try:
        resp = httpx.get(FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        item = (resp.json().get("data") or [])[0]
        value = int(item["value"])
        classification = str(item.get("value_classification", ""))
    except Exception:
        logger.warning("Fear & Greed fetch failed", exc_info=True)
        return None

    score = (value - 50) / 50.0  # 0..100 → -1..+1
    return _store(
        db,
        symbol="GLOBAL",
        source="fear_greed",
        score=score,
        payload={"value": value, "classification": classification},
        article_count=1,
        normalized_items=[f"fg:{value}"],
    )


def _score_articles(
    raw_items: list[dict],
    *,
    keyword_filter: list[str] | None,
) -> tuple[list[dict], dict]:
    """Score and dedup a list of {url, headline, summary} items. Returns
    (scored_articles, feed_stats). Each scored_article is:
        {"url": ..., "headline": ..., "summary": ..., "score": float,
         "normalized": str}
    Dedup keys: URL when present, else normalized headline."""
    try:
        analyzer = _vader()
    except ImportError:
        logger.warning("vaderSentiment missing — cannot score articles")
        return [], {"vader_available": False}

    seen_urls: set[str] = set()
    seen_norms: set[str] = set()
    out: list[dict] = []

    for art in raw_items:
        url = str(art.get("url") or "").strip()
        headline = str(art.get("headline") or "").strip()
        summary = str(art.get("summary") or "").strip()
        if not headline:
            continue

        if keyword_filter is not None:
            text_lower = (headline + ". " + summary).lower()
            if not any(k in text_lower for k in keyword_filter):
                continue

        # Dedup: URL first, then normalised headline.
        if url and url in seen_urls:
            continue
        norm = normalize_headline(headline)
        if not url and norm in seen_norms:
            continue
        if norm in seen_norms:
            continue
        if url:
            seen_urls.add(url)
        seen_norms.add(norm)

        try:
            compound = float(
                analyzer.polarity_scores(headline + ". " + summary)["compound"]
            )
        except Exception:
            continue
        out.append({
            "url": url, "headline": headline, "summary": summary,
            "score": compound, "normalized": norm,
        })

    return out, {"vader_available": True}


def refresh_rss(db: "Session", symbol: str) -> SentimentSnapshot | None:
    """Fetch RSS feeds, filter by symbol keywords, score with VADER."""
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser missing — skipping RSS sentiment")
        return None

    symbol = symbol.upper()
    keywords = [k.lower() for k in SYMBOL_KEYWORDS.get(symbol, [])]
    if not keywords:
        return None

    raw_items: list[dict] = []
    feed_stats: dict[str, int] = {}
    for url in DEFAULT_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            feed_stats[url] = -1
            continue
        for entry in feed.entries[:30]:
            raw_items.append({
                "url": str(getattr(entry, "link", "")).strip(),
                "headline": str(getattr(entry, "title", "")).strip(),
                "summary": str(getattr(entry, "summary", "")).strip(),
            })
        feed_stats[url] = len(feed.entries[:30])

    scored, meta = _score_articles(raw_items, keyword_filter=keywords)
    if not scored:
        return None

    score = sum(a["score"] for a in scored) / len(scored)
    payload = {
        "count": len(scored),
        "feeds": feed_stats,
        "articles": [
            {"url": a["url"], "headline": a["headline"],
             "summary": a["summary"][:300], "score": round(a["score"], 4)}
            for a in scored[:30]
        ],
    }
    metadata = {"feed_stats": feed_stats, **meta}
    return _store(
        db,
        symbol=symbol,
        source="rss",
        score=score,
        payload=payload,
        article_count=len(scored),
        normalized_items=[a["normalized"] for a in scored],
        metadata=metadata,
    )


def refresh_alpaca_news(db: "Session", symbol: str) -> SentimentSnapshot | None:
    """Fetch Alpaca's curated news for `symbol` and score with VADER.
    Server-side symbol tagging means we don't need keyword filtering."""
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        return None

    symbol = symbol.upper()
    api_symbol = symbol.replace("/", "")  # BTC/USD -> BTCUSD
    try:
        resp = httpx.get(
            ALPACA_NEWS_URL,
            params={"symbols": api_symbol, "limit": 50, "sort": "desc"},
            headers={
                "APCA-API-KEY-ID": settings.alpaca_api_key,
                "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        articles = resp.json().get("news") or []
    except Exception:
        logger.warning("Alpaca news fetch failed for %s", symbol, exc_info=True)
        return None

    raw_items = [
        {
            "url": str(art.get("url") or art.get("id") or "").strip(),
            "headline": str(art.get("headline") or "").strip(),
            "summary": str(art.get("summary") or "").strip(),
        }
        for art in articles
    ]
    scored, meta = _score_articles(raw_items, keyword_filter=None)
    if not scored:
        return None

    score = sum(a["score"] for a in scored) / len(scored)
    payload = {
        "count": len(scored),
        "articles": [
            {"url": a["url"], "headline": a["headline"],
             "summary": a["summary"][:300], "score": round(a["score"], 4)}
            for a in scored[:30]
        ],
    }
    return _store(
        db,
        symbol=symbol,
        source="alpaca_news",
        score=score,
        payload=payload,
        article_count=len(scored),
        normalized_items=[a["normalized"] for a in scored],
        metadata=meta,
    )


def refresh_price_action(db: "Session", symbol: str) -> SentimentSnapshot | None:
    """Read closed candles and compute a momentum + range-position score
    in [-1, +1]. Always fresh — never depends on external services."""
    from app.models.candle import Candle

    symbol = symbol.upper()
    latest = (
        db.query(Candle)
        .filter(Candle.symbol == symbol, Candle.is_closed == True)  # noqa: E712
        .order_by(desc(Candle.open_time))
        .first()
    )
    if latest is None:
        return None

    rows = (
        db.query(Candle)
        .filter(
            Candle.symbol == symbol,
            Candle.interval == latest.interval,
            Candle.is_closed == True,  # noqa: E712
        )
        .order_by(desc(Candle.open_time))
        .limit(20)
        .all()
    )
    if len(rows) < 6:
        return None

    closes = [float(r.close) for r in reversed(rows)]
    last = closes[-1]
    earlier = closes[-6]
    if earlier <= 0:
        return None

    pct = (last - earlier) / earlier
    momentum = math.tanh(pct * 30)

    hi = max(closes)
    lo = min(closes)
    rng_pos = 0.0 if hi == lo else ((last - lo) / (hi - lo)) * 2 - 1

    score = max(-1.0, min(1.0, 0.7 * momentum + 0.3 * rng_pos))

    payload = {
        "last_close": last,
        "close_5_ago": earlier,
        "pct_change_5bar": round(pct, 6),
        "range_position": round(rng_pos, 4),
        "momentum": round(momentum, 4),
        "interval": latest.interval,
        "samples": len(closes),
    }
    return _store(
        db,
        symbol=symbol,
        source="price_action",
        score=score,
        payload=payload,
        article_count=len(closes),
        normalized_items=[f"pa:{round(last, 4)}"],
    )


# ── Read API (public surface used by trading_cycle) ───────────────────────


def weighted_score(
    db: "Session",
    symbol: str,
    *,
    half_life_minutes: float = 90.0,
    lookback_hours: float = 24.0,
) -> float | None:
    """Source-and-recency-weighted score:
        weight_i = exp(-age_min / half_life) * source_weight
        score    = sum(score_i * weight_i) / sum(weight_i)
    Returns None if no data in the lookback window."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    rows = (
        db.query(SentimentSnapshot)
        .filter(
            SentimentSnapshot.symbol.in_([symbol.upper(), "GLOBAL"]),
            SentimentSnapshot.created_at >= cutoff,
        )
        .all()
    )
    if not rows:
        return None

    total_w = 0.0
    total_ws = 0.0
    for r in rows:
        created = r.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_min = (now - created).total_seconds() / 60.0
        recency = math.exp(-age_min / half_life_minutes)
        sw = float(r.source_weight or SOURCE_WEIGHTS.get(r.source, DEFAULT_SOURCE_WEIGHT))
        w = recency * sw
        total_w += w
        total_ws += w * float(r.score)

    if total_w <= 0:
        return None
    return total_ws / total_w


def source_breakdown(
    db: "Session",
    symbol: str,
    *,
    lookback_hours: float = 24.0,
) -> dict[str, dict]:
    """Per-source view of the latest snapshot for diagnostics. Returns
    `{source: {"score": float, "label": str, "age_min": float,
              "weight": float, "article_count": int}}`."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    rows = (
        db.query(SentimentSnapshot)
        .filter(
            SentimentSnapshot.symbol.in_([symbol.upper(), "GLOBAL"]),
            SentimentSnapshot.created_at >= cutoff,
        )
        .order_by(desc(SentimentSnapshot.created_at))
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        if r.source in out:
            continue  # only the most recent per source
        created = r.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        out[r.source] = {
            "score": float(r.score),
            "label": r.label,
            "age_min": round((now - created).total_seconds() / 60.0, 2),
            "weight": float(r.source_weight or SOURCE_WEIGHTS.get(r.source, DEFAULT_SOURCE_WEIGHT)),
            "article_count": int(r.article_count or 0),
        }
    return out


def latest_raw_score(db: "Session", symbol: str) -> float | None:
    """Most recent per-symbol (non-GLOBAL) score, unweighted. Used for the
    `sentiment_score` column on BotDecision as the audit trail."""
    row = (
        db.query(SentimentSnapshot)
        .filter(SentimentSnapshot.symbol == symbol.upper())
        .order_by(desc(SentimentSnapshot.created_at))
        .first()
    )
    return float(row.score) if row else None


def latest_news_articles(
    db: "Session",
    symbol: str,
    *,
    limit: int = 8,
    lookback_hours: float = 12.0,
) -> list[dict]:
    """Top-N unique news articles across alpaca_news + rss for the symbol,
    most recent first, deduplicated by normalised headline."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    rows = (
        db.query(SentimentSnapshot)
        .filter(
            SentimentSnapshot.symbol == symbol.upper(),
            SentimentSnapshot.source.in_(["alpaca_news", "rss"]),
            SentimentSnapshot.created_at >= cutoff,
        )
        .order_by(desc(SentimentSnapshot.created_at))
        .limit(20)  # newest few snapshots — that's enough articles to rank
        .all()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r.raw_payload or "{}")
        except json.JSONDecodeError:
            continue
        for art in payload.get("articles", []):
            headline = str(art.get("headline") or "").strip()
            if not headline:
                continue
            norm = normalize_headline(headline)
            if norm in seen:
                continue
            seen.add(norm)
            out.append({
                "headline": headline,
                "summary": str(art.get("summary") or "")[:300],
                "score": float(art.get("score") or 0),
                "url": str(art.get("url") or ""),
                "source": r.source,
            })
            if len(out) >= limit:
                return out
    return out


def latest_fear_greed(db: "Session") -> dict | None:
    """Most recent F&G snapshot's raw payload + score, or None."""
    row = (
        db.query(SentimentSnapshot)
        .filter(SentimentSnapshot.source == "fear_greed")
        .order_by(desc(SentimentSnapshot.created_at))
        .first()
    )
    if row is None:
        return None
    try:
        payload = json.loads(row.raw_payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "value": payload.get("value"),
        "classification": payload.get("classification"),
        "score": float(row.score),
        "created_at": row.created_at,
    }


# ── Background refresher ──────────────────────────────────────────────────


class SentimentRefresher:
    """Periodic task. Ticks every FAST_TICK_SECONDS (15 min) and calls each
    per-source refresh helper, each of which self-skips unless its MIN_GAP
    has elapsed since its last run. Heartbeat-logs every tick."""

    def __init__(self, tenant_registry: "TenantRegistry") -> None:
        self.tenant_registry = tenant_registry
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_run: dict[tuple[str, str], datetime] = {}

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="sentiment-refresher")
        logger.info("SentimentRefresher started (%ds cadence)", FAST_TICK_SECONDS)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._tick)
            except Exception:
                logger.exception("SentimentRefresher tick failed")
            try:
                await asyncio.sleep(FAST_TICK_SECONDS)
            except asyncio.CancelledError:
                raise

    def _tick(self) -> None:
        from app.db.session import SessionLocal

        symbols = self.tenant_registry.union_of_tracked_symbols()
        if not symbols:
            return

        db = SessionLocal()
        try:
            sources = ("fear_greed", "rss", "alpaca_news", "price_action")
            wrote = {s: 0 for s in sources}
            skipped = {s: 0 for s in sources}

            if self._due("fear_greed", "GLOBAL"):
                if refresh_fear_greed(db) is not None:
                    wrote["fear_greed"] += 1
                else:
                    skipped["fear_greed"] += 1
                self._mark_run("fear_greed", "GLOBAL")

            for symbol in symbols:
                if self._due("rss", symbol):
                    if refresh_rss(db, symbol) is not None:
                        wrote["rss"] += 1
                    else:
                        skipped["rss"] += 1
                    self._mark_run("rss", symbol)
                if self._due("alpaca_news", symbol):
                    if refresh_alpaca_news(db, symbol) is not None:
                        wrote["alpaca_news"] += 1
                    else:
                        skipped["alpaca_news"] += 1
                    self._mark_run("alpaca_news", symbol)
                if self._due("price_action", symbol):
                    if refresh_price_action(db, symbol) is not None:
                        wrote["price_action"] += 1
                    else:
                        skipped["price_action"] += 1
                    self._mark_run("price_action", symbol)

            logger.info(
                "Sentiment tick: wrote fg=%d rss=%d news=%d pa=%d | "
                "skipped fg=%d rss=%d news=%d pa=%d | symbols=%d",
                wrote["fear_greed"], wrote["rss"], wrote["alpaca_news"], wrote["price_action"],
                skipped["fear_greed"], skipped["rss"], skipped["alpaca_news"], skipped["price_action"],
                len(symbols),
            )
        finally:
            db.close()

    def _due(self, source: str, symbol: str) -> bool:
        last = self._last_run.get((source, symbol))
        if last is None:
            return True
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= MIN_GAP.get(source, FAST_TICK_SECONDS)

    def _mark_run(self, source: str, symbol: str) -> None:
        self._last_run[(source, symbol)] = datetime.now(timezone.utc)
