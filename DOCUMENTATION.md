# Claude Trader v2 — Full System Documentation

A multi-tenant, AI-driven crypto trading bot. Each user signs in via Clerk,
configures a single trading symbol, and the bot autonomously cycles through:
*pull market data → ask Claude for a verdict → risk-validate → submit a real
order to Alpaca's paper or live account*. All while the next user's bot does
the same thing for their symbol, sharing the same backend infrastructure.

---

## Table of contents

1. [What this is](#1-what-this-is)
2. [Tech stack](#2-tech-stack)
3. [Repository layout](#3-repository-layout)
4. [Two-plane architecture](#4-two-plane-architecture)
5. [The five long-running background tasks](#5-the-five-long-running-background-tasks)
6. [The decision pipeline, end-to-end](#6-the-decision-pipeline-end-to-end)
7. [Data model](#7-data-model)
8. [Sentiment system](#8-sentiment-system)
9. [Technical analysis](#9-technical-analysis)
10. [MarketContext: what the AI sees](#10-marketcontext-what-the-ai-sees)
11. [AI prompt and decision provider](#11-ai-prompt-and-decision-provider)
12. [Risk service](#12-risk-service)
13. [Order execution and Alpaca specifics](#13-order-execution-and-alpaca-specifics)
14. [Multi-tenancy: what's isolated, what's shared](#14-multi-tenancy-whats-isolated-whats-shared)
15. [Authentication](#15-authentication)
16. [Real-time updates](#16-real-time-updates)
17. [Frontend architecture](#17-frontend-architecture)
18. [Configuration: settings vs BotConfig](#18-configuration-settings-vs-botconfig)
19. [Lifecycle: cold start to graceful shutdown](#19-lifecycle-cold-start-to-graceful-shutdown)
20. [Testing strategy](#20-testing-strategy)
21. [Error handling philosophy](#21-error-handling-philosophy)
22. [Trade-offs and limitations](#22-trade-offs-and-limitations)

---

## 1. What this is

Claude Trader v2 is a parallel rewrite of an earlier single-tenant trading
bot (`../crypto_bot/` + `../dashboard/`). The v2 design supports multiple
independent users on a single backend process, each with their own session
state, decision history, and trading parameters.

The user-facing flow is intentionally simple:

1. Sign up with Clerk.
2. Pick a symbol (default `BTC/USD`), interval (`30m` / `1h` / `4h`), and risk
   parameters (position size %, min confidence, SL/TP caps).
3. Click **Start**.
4. The bot autonomously cycles: every interval it asks Claude what to do, and
   if Claude is confident enough, places a real market buy at Alpaca's paper
   endpoint with attached SL and TP exit logic. The dashboard live-updates
   with decisions, fills, equity curve, and position state.
5. Click **Stop** to flatten any open position and end the session.

Behind that, the system is doing a lot of work continuously — pulling candles
and news, scoring sentiment, refreshing technicals, watching for SL/TP
triggers, streaming live prices. The point of this document is to explain
exactly how that all fits together.

## 2. Tech stack

**Backend (`v2/api/`)**

- **Python 3.13**
- **FastAPI** — HTTP + WebSocket routes, dependency injection, Pydantic schemas
- **SQLAlchemy 2.x** — ORM and DB session management
- **SQLite** for dev (`crypto_bot_v2.db`); the schema is portable to Postgres
- **APScheduler** — the 60-second heartbeat that drives decision cycles
- **anthropic** SDK — Claude API client
- **alpaca-py** — Alpaca SDK (paper + live trading, crypto market data)
- **vaderSentiment** — lexicon-based sentiment scorer, patched with crypto vocabulary
- **feedparser** — RSS ingestion
- **pyjwt** — Clerk JWT verification via JWKS
- **pytest** + **pytest-asyncio** — test suite (96 tests, ~2.5s runtime)

**Frontend (`v2/web/`)**

- **Next.js 15** with the app router
- **React 19**
- **Clerk** for authentication
- **shadcn/ui** + **Tailwind CSS 4** for components and styling
- **SWR** for server state with polling and JWT-aware fetching
- **lightweight-charts** for the equity curve
- **TypeScript 5**

## 3. Repository layout

```
v2/
├── README.md                  # quick-start
├── CLAUDE.md                  # operational guidance for Claude Code agents
├── DOCUMENTATION.md           # this file
├── .gitignore
│
├── api/                       # FastAPI backend
│   ├── pytest.ini
│   ├── requirements.txt
│   ├── .env(.example)
│   ├── app/
│   │   ├── main.py            # FastAPI app + lifespan that wires everything
│   │   ├── state.py           # process-wide singletons
│   │   ├── config.py          # immutable settings loaded from .env
│   │   ├── event_bus.py       # in-process pub/sub keyed by tenant_id
│   │   ├── api/
│   │   │   └── routes/        # HTTP + WS endpoints
│   │   ├── auth/
│   │   │   ├── clerk.py       # JWKS-cached JWT verification
│   │   │   └── dependencies.py# current_tenant FastAPI dependency
│   │   ├── db/
│   │   │   ├── session.py     # engine + SessionLocal factory
│   │   │   └── init_db.py     # create_all (with optional drop)
│   │   ├── models/            # one ORM class per table
│   │   ├── services/
│   │   │   ├── orchestration/ # tenant_registry, tick_scheduler,
│   │   │   │                  # order_queue, execution_worker, oco_trigger
│   │   │   ├── shared/        # alpaca_ws, market_state, market_data_service,
│   │   │   │                  # sentiment_service, technical_service,
│   │   │   │                  # market_context_service, claude_provider,
│   │   │   │                  # decision_provider, data_feeds
│   │   │   └── tenant/        # trading_cycle, risk_service, session_service,
│   │   │                      # position_service
│   │   └── util/
│   │       └── dates.py       # iso_utc() — UTC ISO formatter
│   └── tests/                 # pytest suite, see §20
│
└── web/                       # Next.js frontend
    ├── package.json
    ├── middleware.ts          # Clerk gate on every route except sign-in/up
    ├── next.config.ts
    ├── app/
    │   ├── (app)/             # signed-in route group
    │   ├── (auth)/            # sign-in / sign-up
    │   ├── layout.tsx         # ClerkProvider wrapper
    │   └── globals.css
    ├── components/            # ui/, overview/, settings/, trades/, shared/
    ├── hooks/                 # use-api, use-auth-fetch, use-tenant-ws
    └── lib/                   # api types, format helpers
```

## 4. Two-plane architecture

The backend is deliberately split into two planes so that expensive shared
work (a single market-data WebSocket, one news fetch per symbol, one
Anthropic client) is done once for everybody, while per-user state
(positions, balance, decisions) is strictly isolated.

```
┌────────────────────────── Shared plane (singletons) ──────────────────────────┐
│                                                                                │
│  AlpacaMarketWS ── streams quotes/trades for the union of every running       │
│                    tenant's symbol → MarketStateStore (in-memory price cache) │
│                                                                                │
│  ClaudeDecisionProvider ── one anthropic.Anthropic client                      │
│                                                                                │
│  SentimentRefresher ── 15-min cadence, refreshes 4 sources for every          │
│                        tracked symbol → sentiment_snapshots table             │
│                                                                                │
│  OrderQueue ── async FIFO; every tenant's orders go through one queue         │
│  ExecutionWorker ── single consumer, submits to Alpaca, writes Trade rows     │
│                                                                                │
│  OcoTriggerService ── 2-second poll of active SL/TP Order rows                │
│                                                                                │
│  TenantRegistry ── lazy { tenant_id → TradingCycleService }                   │
│  TickScheduler ── 60-second heartbeat scans bot_configs → fires due cycles   │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        │  (one-per-tenant on demand)
                                        ▼
┌────────────────────────── Per-tenant plane ──────────────────────────────────┐
│                                                                               │
│  TradingCycleService ── orchestrates one decision cycle for one tenant       │
│  RiskService         ── stateless validator, last gate before order queue    │
│                                                                               │
│  Session / position / order state lives entirely in the DB,                  │
│  scoped by tenant_id on every row.                                           │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

The shared plane is built once during `lifespan` startup in `main.py` and
stored on `app.state` (`state.execution_worker`, `state.market_ws`, etc.).
The per-tenant plane is built lazily — `TenantRegistry.ensure(tenant_id, cfg)`
constructs the `TradingCycleService` the first time a tenant is seen and
caches it for the life of the process.

State that **could** have been per-tenant in memory (open position,
session balance, etc.) deliberately lives in the DB instead. This means:
- Process restarts don't lose anything.
- Multiple workers in the future can share state without distributed-cache complexity.
- Tests can reset cleanly with one `Base.metadata.drop_all()` call.

## 5. The five long-running background tasks

When uvicorn starts, exactly **five** independent autonomous loops kick off,
each backed by an `asyncio.Task` (or APScheduler job) running concurrently
in the same event loop. Anything the API does on its own — without an
incoming HTTP request — happens inside one of these.

### 5.1 ExecutionWorker (`services/orchestration/execution_worker.py`)

Single consumer of the `OrderQueue`. Loop:

```python
while not stop:
    job = await queue.get()
    handle(job)               # dispatch by job.type
    queue.task_done()
```

Job types:
- `PLACE_MARKET_BUY` — submits a real Alpaca market buy with `notional=current_balance × size_pct/100`. Waits for the fill, reads `filled_avg_price` and `filled_qty` from Alpaca's response, writes a `Trade` + `Position` row, debits the session's virtual balance, then enqueues `PLACE_OCO_SELL` to record local SL/TP legs.
- `PLACE_OCO_SELL` — writes two `Order` rows (`STOP_LOSS` and `TAKE_PROFIT`) with a shared `oco_group_id`. **No Alpaca call** because Alpaca crypto doesn't support server-side bracket orders.
- `EXIT_POSITION_MARKET` — cancels active SL/TP legs, submits an Alpaca market sell for the full position quantity, writes the SELL `Trade` row, closes the `Position`, credits the balance.
- `CANCEL_OPEN_BUY_ORDERS` — best-effort cancel of pending Alpaca orders + flips local statuses.
- `SYNC_ORDER_STATUS` — placeholder for live-mode reconciliation. No-op for paper.

Each handler runs in a worker thread (`asyncio.to_thread`) because it does
synchronous DB + REST work. It opens its own `SessionLocal()` so there's no
session sharing across the queue boundary.

If Alpaca's response is async (paper is usually synchronous, live can lag),
`_fill_price` falls back to the last WS-cached price so accounting doesn't
stall.

### 5.2 AlpacaMarketWS (`services/shared/alpaca_ws.py`)

One persistent WebSocket connection to
`wss://stream.data.alpaca.markets/v1beta3/crypto/us`. Auths once with the
process's Alpaca keys, then subscribes to the **union** of every running
tenant's symbol via `state.tenant_registry.union_of_tracked_symbols()`.

Three event types are processed:
- **Quote** (`T:"q"`) — bid + ask; we average to a mid for `last_price`.
- **Trade** (`T:"t"`) — actual print; overrides the quote-mid for that symbol.
- **Bar** (`T:"b"`) — 1-minute close; used as a last-price fallback.

Every event updates `MarketStateStore`, which is just a thread-safe dict of
`SymbolMarketState` records. All consumers (`ExecutionWorker`,
`OcoTriggerService`, `TradingCycleService`'s "now" price) read from this
store instead of hitting REST every time.

Reconnection is automatic with exponential backoff. When a tenant's symbol
changes or a new tenant comes online, `bot_control.start` calls
`set_subscription(...)` + `await reconnect()` — the loop drops the current
socket and rebuilds with the fresh symbol set.

### 5.3 OcoTriggerService (`services/orchestration/oco_trigger.py`)

Wakes up every **2 seconds** and runs:

```sql
SELECT orders.* FROM orders
JOIN trading_sessions ON trading_sessions.id = orders.session_id
WHERE orders.status = 'active'
  AND orders.type IN ('STOP_LOSS', 'TAKE_PROFIT')
  AND trading_sessions.status IN ('RUNNING', 'STOPPING')
```

For each active leg, reads the symbol's `last_price` from the in-memory
`MarketStateStore` and checks:
- `STOP_LOSS`: trigger if `price ≤ stop_price`
- `TAKE_PROFIT`: trigger if `price ≥ price`

If a leg fires, it does an **atomic claim**:

```python
affected = (db.query(Order)
              .filter(Order.id == row.id, Order.status == "active")
              .update({"status": "triggered"}))
db.commit()
if not affected:
    continue   # another caller (e.g. retry, second instance) won the race
```

Then enqueues `EXIT_POSITION_MARKET` on the `OrderQueue`. The
`ExecutionWorker` handles the actual market sell.

This service exists because Alpaca crypto doesn't support server-side
bracket / OCO orders (only equities). On a binance/bybit/kraken backend,
this whole service could be deleted — the exchange would handle it.

### 5.4 SentimentRefresher (`services/shared/sentiment_service.py`)

Wakes up every **15 minutes** (`FAST_TICK_SECONDS`). For each symbol in the
union, calls each per-source refresh function — but each source has its own
`MIN_GAP` and self-skips if it ran recently:

| Source        | MIN_GAP  |
|---------------|----------|
| `fear_greed`  | 60 min   |
| `rss`         | 15 min   |
| `alpaca_news` | 15 min   |
| `price_action`| 15 min   |

Heartbeat-logs every tick:

```
Sentiment tick: wrote fg=0 rss=1 news=1 pa=1 | skipped fg=1 rss=0 news=0 pa=0 | symbols=1
```

so a dead source becomes immediately visible.

See §8 for what each source actually does.

### 5.5 TickScheduler (`services/orchestration/tick_scheduler.py`)

APScheduler job running every **60 seconds**. Each tick:

1. Queries `bot_configs WHERE running = true`.
2. For each row whose `next_run_at <= now`, calls `tenant_registry.ensure(tenant_id, cfg)` and adds it to the "due" list.
3. Advances `cfg.next_run_at = now + interval_minutes` **before** firing the cycle, so a slow cycle can't trigger itself again.
4. Fires all due cycles concurrently bounded by `MAX_CONCURRENT_CYCLES = 5`:

```python
async with self._semaphore:
    services = self.tenant_registry.get(tenant_id)
    db = SessionLocal()
    try:
        await services.cycle_service.run(db, config)
    finally:
        db.close()
```

This is the only thing that initiates trading-decision activity — without
it, the bot would just sit there.

## 6. The decision pipeline, end-to-end

Every cycle for a tenant runs the same sequence (`TradingCycleService.run`):

```
1. Confirm session.status == RUNNING
2. market_data_service.refresh(db, symbol, interval)
       → Alpaca CryptoBarsRequest → upsert closed candles into `candles` table
3. MarketContextService.build(db, session, config)
       → reads sentiment, news, F&G, candles, computes technicals,
         loads open position + memory, persists a MarketContextSnapshot
       → returns a MarketContext object
4. decision_provider.decide(ctx, min_confidence, max_position_pct)
       → ClaudeDecisionProvider builds the prompt from MarketContext,
         calls Anthropic API, parses strict JSON
       → returns a Decision (action, confidence, reason, stop_loss_pct, take_profit_pct)
5. RiskService.evaluate(decision, session, config, position, price, balance)
       → enforces every safety gate (kill switch, session loss cap,
         position-state rules, confidence threshold, SL/TP clamping)
       → returns RiskCheck (approved + final action + clamped SL/TP)
6. Persist BotDecision row
       → action = post-risk verdict, approved + rejection_reason
       → sentiment_score and weighted_sentiment_score for audit
7. MarketContextService.attach_decision_id(db, ctx, bd.id)
       → links the persisted snapshot back to the BotDecision row,
         enabling lossless replay of any cycle later
8. If approved + actionable, enqueue an OrderQueue job:
       BUY → PLACE_MARKET_BUY (size_pct, sl_pct, tp_pct)
       SELL → EXIT_POSITION_MARKET (position_id, reason="AI_SELL")
       HOLD → nothing
```

The cycle never directly places an order — it only enqueues a job. Then
**ExecutionWorker** (a different background task) picks it up and submits
the actual Alpaca order. This separation is deliberate:

- The decision loop stays cheap and reproducible. A cycle's `BotDecision`
  row + `MarketContextSnapshot` can be replayed in tests without moving any
  money.
- If the queue backs up, decisions still record cleanly — they just don't
  execute until the worker catches up.
- One job is handled by exactly one worker, so an order can't double-fire.

## 7. Data model

Every row carries `tenant_id` (or chains to one via foreign keys) so a
single SQL query — `WHERE tenant_id = ?` — gives you everything one user
owns and nothing they don't. No application-level filtering trickery.

| Table                       | Purpose                                                                              | Key columns                                                                                                                              |
|-----------------------------|--------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| `users`                     | One per Clerk identity                                                               | `clerk_user_id` UNIQUE                                                                                                                   |
| `tenants`                   | One per user (kept separate for future team support)                                 | `user_id`                                                                                                                                |
| `bot_configs`               | Per-tenant trading parameters; `running` + `next_run_at` are runtime state          | `tenant_id` UNIQUE, `running`, `next_run_at`, `symbol`, `interval`, `mode`, `position_size_pct`, `min_confidence`, SL/TP, `model_name`   |
| `trading_sessions`          | One open-to-close run per tenant; statuses form a strict state machine               | `tenant_id`, `status`, `starting_balance`, `current_balance`, `session_pnl`, timestamps                                                  |
| `bot_decisions`             | One row per decision cycle (approved or not) for replay + audit                      | `tenant_id`, `session_id`, `action`, `confidence`, `reason`, `approved`, `rejection_reason`, `sentiment_score`, `weighted_sentiment_score` |
| `positions`                 | Open-to-close position round-trips                                                   | `session_id`, `symbol`, `status`, `entry_price`, `exit_price`, `size_pct`, `realised_pnl`, `unrealised_pnl`, `exit_reason`               |
| `trades`                    | Individual fills (one BUY + one SELL per round-trip)                                 | `session_id`, `position_id`, `side`, `order_type`, `price`, `quantity`, `fee`, `alpaca_order_id`                                         |
| `orders`                    | Pending/active SL/TP legs (no Alpaca counterpart for crypto)                         | `position_id`, `side`, `type`, `status`, `stop_price`, `price`, `oco_group_id`                                                           |
| `equity_snapshots`          | Per-session equity-curve samples                                                     | `session_id`, `balance`, `unrealised_pnl`, `total_equity`, `created_at`                                                                  |
| `candles`                   | Closed OHLCV bars from Alpaca, deduped by (symbol, interval, open_time)              | `symbol`, `interval`, `open_time`, OHLC + `volume`                                                                                       |
| `sentiment_snapshots`       | Per-source sentiment readings; never overwritten, fade out via decay                 | `source`, `symbol`, `score`, `source_weight`, `payload_hash`, `normalized_hash`, `article_count`, `raw_payload`, `snapshot_metadata`     |
| `market_context_snapshots`  | Frozen replay-able context per cycle, FK to BotDecision                              | `session_id`, `decision_id`, `weighted_sentiment_score`, `source_breakdown`, `raw_news_context`, `technical_context`, etc.               |
| `technical_snapshots`       | Per-cycle technical indicator values                                                 | `session_id`, `ema_9`, `ema_21`, `rsi_14`, `macd`, `macd_signal`, `volume_change_pct`, `price_change_pct`, `trend_label`                 |

`bot_decisions` and `market_context_snapshots` are linked: when a cycle
writes a BotDecision, it also fills in `market_context_snapshots.decision_id`
so you can pull up "the exact context Claude saw when it issued decision #137."

## 8. Sentiment system

Four sources, each writing its own `SentimentSnapshot` rows, each with a
multiplicative weight applied at score-blend time:

| Source         | Weight | Cadence | What it does                                                                                              |
|----------------|--------|---------|-----------------------------------------------------------------------------------------------------------|
| `alpaca_news`  | 1.3    | 15 min  | Hits Alpaca's news API server-side-tagged per symbol; VADER-scores headline + summary                    |
| `price_action` | 1.2    | 15 min  | Pure-python: `0.7 × tanh(5-bar return × 30) + 0.3 × range_position` from the last 20 candles             |
| `fear_greed`   | 0.8    | 60 min  | alternative.me F&G index; `(value − 50) / 50` to map 0..100 → -1..+1                                      |
| `rss`          | 0.7    | 15 min  | 5 crypto-news feeds (cryptopotato, news.bitcoin.com, coindesk, cointelegraph, decrypt), keyword-filtered, VADER-scored |

VADER is patched at module-load with a **crypto lexicon** (`pump:+2.5`,
`dump:-3.0`, `rugpull:-3.0`, `etf:+1.5`, `rekt:-2.5`, etc.) so headlines like
"Bitcoin pumps on ETF approval" don't score 0 the way they would with stock
VADER.

### Weighted score formula

```
weight_i = exp(-age_min / half_life) × source_weight
score    = Σ(score_i × weight_i) / Σ(weight_i)
```

Defaults: `half_life_minutes = 90`, `lookback_hours = 24`. Snapshots
older than the lookback are excluded entirely.

This means:
- A snapshot from 90 minutes ago contributes half as much as a fresh one (recency decay).
- Within the same age, an `alpaca_news` snapshot contributes 1.3 / 0.7 ≈ 1.86× as much as an `rss` one (source weight).
- F&G's daily updates fade gradually over 24 hours rather than hard-cutting after one day.

### Deduplication

Two layers, applied in `_store()`:

1. **Payload hash** — SHA-256 of the full `raw_payload` JSON. If identical to the previous snapshot for the same `(source, symbol)`, skip the write entirely. This catches the "F&G hasn't changed in 8 hours" case.
2. **Normalized hash** — SHA-256 of the sorted unique normalized headlines (`normalize_headline()` = lowercase + strip punctuation + collapse whitespace). Catches the case where article order changed but content is identical.

Inside the news fetchers, articles are deduped in-memory **before** scoring:
- First by **URL** when present.
- Then by **normalized headline** as a fallback.

Together this means the table grows only when something actually changed.

### Read API

- `weighted_score(db, symbol)` → the blended number Claude sees in the prompt.
- `latest_raw_score(db, symbol)` → the freshest single per-symbol snapshot, recorded on `BotDecision.sentiment_score` for audit.
- `source_breakdown(db, symbol)` → `{source: {score, label, age_min, weight, article_count}}` for the prompt's transparency block and the dashboard.
- `latest_news_articles(db, symbol)` → top-N unique deduped article dicts for the prompt's news block.
- `latest_fear_greed(db)` → most recent F&G payload + score.

## 9. Technical analysis

`services/shared/technical_service.py`. Pure Python, no numpy. Reads the
last 60 closed candles for the symbol's most recent interval and computes:

- **EMA-9** and **EMA-21** — standard exponential moving averages, seeded with a simple average of the first `period` values.
- **RSI-14** — Wilder's smoothing.
- **MACD** — 12-EMA minus 26-EMA, signal line is 9-EMA of the MACD line.
- **Price change %** — last close vs previous close.
- **Volume change %** — last volume vs previous volume.
- **Trend label** — `"bullish"` if `EMA9 > EMA21 AND price_change_pct > 0`, `"bearish"` if both inverted, else `"neutral"`.

These get written to `technical_snapshots` per cycle and fed into the AI
prompt's `=== TECHNICAL INDICATORS ===` block.

If there isn't enough history yet (< 6 candles for momentum, < 35 for MACD),
the relevant fields stay `None` and the rest still compute. The prompt
formatter prints `—` for missing values, so the AI sees what's available
without lying about what isn't.

## 10. MarketContext: what the AI sees

`MarketContextService.build()` is the single entry point that assembles
**every input** the AI sees and persists a frozen replay-able snapshot. The
returned `MarketContext` dataclass contains:

```python
weighted_sentiment_score: float | None       # blended -1..+1
source_breakdown:        dict[str, dict]     # {source: {score, label, age_min, weight, article_count}}
news_articles:           list[dict]          # top 8, deduped, with url/headline/summary/score/source
fear_greed:              dict | None         # {value, classification, score, created_at}

candles:                 list[dict]          # last 20 closed OHLCV
technicals:              TechnicalReading    # EMA/RSI/MACD/etc.

open_position:           dict | None         # entry, qty, size, SL, TP, unrealised_pnl
session_pnl:             float
session_pnl_pct:         float
current_balance:         float

previous_decisions:      list[dict]          # last 5 with HOLD compression
recent_trades:           list[dict]          # last 3 fills
```

### HOLD compression

Trading bots can sit in long stretches of low-conviction HOLDs. Sending the
last 5 verbatim to Claude is wasteful — they're all variations of "tight
range, no clear signal." `_compress_holds()`:

1. Walks backwards from the most recent decision.
2. Finds the longest **tail** of HOLDs.
3. If 3 or more, and they share the same first-six-words of `reason`, collapses them into one summary line:
   `"[N consecutive HOLDs with similar reasoning] <last reason>"`.
4. Non-HOLD decisions and shorter HOLD runs are kept verbatim.

The summary line carries `compressed_count` so the prompt can convey
"you've been HOLD'ing for the last 47 cycles, here's the gist."

### Persistence

The build also writes to two tables before returning:

- `market_context_snapshots` row with the sentiment + news + F&G + technical + memory blocks JSON-encoded into separate columns.
- `technical_snapshots` row with the indicator values broken out as proper columns.

The market-context row's `decision_id` is filled in afterward by
`attach_decision_id()` — called by `TradingCycleService` once the
`BotDecision` row has committed.

This design means **every decision in the database can be replayed**: pull
the `market_context_snapshot` for that decision, reconstruct what the AI
saw, change the prompt or model, and run it again offline.

## 11. AI prompt and decision provider

`services/shared/claude_provider.py::ClaudeDecisionProvider`. Stateless
Anthropic-SDK wrapper. Its `decide()` method takes the `MarketContext`
plus the tenant's `min_confidence` and `max_position_pct` thresholds.

### What goes into the prompt

The system prompt sets the rules:

- "You are a disciplined crypto trading analyst for `<symbol>` on the `<interval>` timeframe."
- Action must respect position state: SELL or HOLD when long, BUY or HOLD when flat.
- Only BUY/SELL when confidence ≥ `min_confidence`.
- `stopLossPct` / `takeProfitPct` are percentages (NOT decimals).
- One sentence reason citing specific evidence.
- Strict JSON output.

The user prompt feeds **raw data**, not labels:

```
=== POSITION & BALANCE ===
Currently FLAT (no open position).
Balance $10,000.00 | Session P&L +$0.00 (+0.00%)

=== TECHNICAL INDICATORS ===
EMA9=77234.1234 EMA21=77198.5532 RSI14=58.2300 MACD=12.4500 signal=8.9100
price_change_pct=0.42% volume_change_pct=8.3% trend=bullish

=== RECENT OHLCV (oldest → newest) ===
  2026-04-25T12:00:00+00:00  O=77100.00  H=77260.00  L=77050.00  C=77234.00  V=132.45
  ... (12 lines)

=== SENTIMENT ===
weighted_score=+0.184 (scale -1..+1, blended across sources with recency + source-quality weights)
  alpaca_news: score=+0.320 label=bullish age=8min weight=1.30 articles=12
  rss:         score=-0.040 label=neutral age=12min weight=0.70 articles=4
  price_action:score=+0.510 label=bullish age=2min weight=1.20 articles=20
  fear_greed:  value=39 (Fear)

=== TOP NEWS HEADLINES ===
  [alpaca_news] Bitcoin breaks above $77k as ETF inflows resume — <summary>... (score +0.62)
  [alpaca_news] BlackRock files for spot ETH ETF amendment...                  (score +0.41)
  ...

=== PREVIOUS DECISIONS (oldest → newest) ===
  HOLD conf=0.45 [OK] — "[3 consecutive HOLDs with similar reasoning] Price in tight range..."
  BUY  conf=0.72 [OK] — "EMA9 crossed above EMA21 with bullish news"

=== RECENT TRADES ===
  BUY qty=0.001234 @ $77100.0000 fee=$0.0900 at 2026-04-25T11:47:00+00:00
```

### Strict JSON output

The model must return:

```json
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0,
  "reason": "...",
  "stopLossPct": 2,
  "takeProfitPct": 4
}
```

`_extract_json` tolerates leading prose or markdown fences and pulls the
first JSON object (or array, as a fallback). `_validate` then:

- Clamps confidence to [0, 1].
- Coerces unknown actions to HOLD.
- Forces HOLD if `action == BUY` and a position is already open (defense-in-depth alongside the system prompt's rule).
- Forces HOLD if `action == SELL` and no position exists.
- Forces HOLD if `confidence < min_confidence` for non-HOLD actions.
- Clamps `stopLossPct` to [0.1, 20] and `takeProfitPct` to [0.1, 40] — the AI can't return a 50% SL.
- Discards AI-suggested `size_pct` entirely. Size is config-controlled.

Failure modes (empty response, garbage JSON, parse error) all return
`Decision(action=HOLD, confidence=0.0, reasoning=<error>)`.

### Position size is no longer AI-controlled

Earlier versions let the model pick `size_pct` 0-N. That's been removed —
now `RiskService` substitutes `config.position_size_pct` for any approved
BUY. The AI can pick *whether* to enter, *how confidently*, and *where the
exits should sit*. It cannot pick how much to risk.

## 12. Risk service

`services/tenant/risk_service.py::RiskService.evaluate()`. The last
gate before an order goes out. Each gate maps to a specific failure mode
that's caught here regardless of what the model said:

1. **Kill switch** — `KILL_SWITCH=true` env var → forces HOLD. Read at evaluation time, no restart needed.
2. **Session not RUNNING** — never trade in `STARTING`/`STOPPING`/`EXITING`/`ENDED`/`ERROR`.
3. **HOLD short-circuit** — if the AI said HOLD, approve and return immediately.
4. **Unknown action** — defensive HOLD.
5. **Session loss cap** — `session_pnl_pct` past `config.max_session_loss_pct` blocks new BUYs (SELL still allowed so the user can flatten).
6. **BUY-while-long** — already holding a position → reject.
7. **SELL-while-flat** — no position to sell → reject.
8. **Confidence threshold** — `confidence < config.min_confidence` → reject BUY.
9. **Size sanity** — ≤ 0 → reject. Otherwise capped by `config.position_size_pct`.
10. **SL/TP sanity** — must be > 0; clamped to `2 × config.stop_loss_pct` and `3 × config.take_profit_pct` so the AI can't override the user's risk parameters by 10×.
11. **Price availability** — no market price → reject (prevents zero-price fills).
12. **Balance check** — virtual balance must cover the proposed notional.

Returns a `RiskCheck`:

```python
@dataclass
class RiskCheck:
    approved: bool
    action: str               # final post-risk verdict
    reason: str               # rejection_reason or "ok"
    confidence: float
    size_pct: float
    stop_loss_pct: float
    take_profit_pct: float
```

Both approved and rejected paths produce a `BotDecision` row — the
rejection reason is preserved for audit and replay.

## 13. Order execution and Alpaca specifics

### Mode and the global safety flag

`BotConfig.mode ∈ {PAPER, LIVE}`. `data_feeds.get_trading_client(paper=...)`
returns the appropriate `alpaca-py.TradingClient`. The global
`ALPACA_PAPER=true` env flag overrides `paper=False` back to `True` —
belt-and-braces so a tenant who picks LIVE in the UI doesn't accidentally
trade real money until the operator explicitly flips that switch.

### Symbol format

Alpaca crypto uses **`BTC/USD`** (with slash). `to_alpaca_symbol()`
normalises legacy `BTCUSDT` and `BTCUSD` inputs at the boundary. The news
endpoint, however, takes the **concatenated** form `BTCUSD` — which is
why `refresh_alpaca_news` does `symbol.replace("/", "")` before the
HTTP call.

### Order shape

```python
MarketOrderRequest(
    symbol="BTC/USD",
    side=OrderSide.BUY,
    notional=1000.0,                  # dollar amount; mutually exclusive with qty
    time_in_force=TimeInForce.GTC,
    client_order_id="ct-buy-...",
)
```

For SELLs we use `qty=position.quantity` instead of `notional`. Never
both at once — Alpaca rejects.

### Crypto bracket / OCO

**Not supported.** Alpaca's bracket orders are equities-only. So:

1. After a BUY fills, `_place_oco_sell` writes two local `Order` rows (`STOP_LOSS` and `TAKE_PROFIT`) sharing an `oco_group_id`.
2. `OcoTriggerService` polls them every 2s.
3. When triggered, it enqueues `EXIT_POSITION_MARKET`, which submits a real Alpaca market sell.

The 2-second poll means SL/TP firing latency is ≤ 2 seconds — fine for
30m/1h/4h cycles with SL 2-5% away, but not adequate for tight scalping.

### Async fills

Paper-mode market orders typically come back with `filled_avg_price` and
`filled_qty` populated synchronously. Live can be async, with the order
arriving in `accepted` state and filling later. `_fill_price` and
`_fill_qty` fall back to the WS-cached last price + the requested qty so
accounting doesn't stall. A `SYNC_ORDER_STATUS` job type is reserved for
later reconciliation but currently a no-op.

### Per-tenant balance accounting

All tenants share **one Alpaca account**. To keep per-tenant P&L isolated,
`TradingSession.starting_balance` / `current_balance` are **virtual**:

- Each session gets a synthetic starting pool from `BotConfig.paper_balance_usd` (default $10,000).
- Real Alpaca fills update that virtual balance at the prices Alpaca reports.
- The Alpaca account's actual cash drifts from `Σ(session balances)` — that's expected.

This works for a single solo developer or a small set of users on one
account. Per-tenant Alpaca API keys (via OAuth or user-provided) is a v3
concern.

## 14. Multi-tenancy: what's isolated, what's shared

| Layer                          | Scope         | Why                                                              |
|--------------------------------|---------------|------------------------------------------------------------------|
| HTTP routes                    | Per-tenant    | `Depends(current_tenant)` resolves Clerk JWT → `Tenant` row      |
| DB rows (configs, sessions, decisions, positions, trades, orders, equity, technical/context snapshots) | Per-tenant | Every row has `tenant_id` (direct or via FK chain)               |
| `TradingCycleService`          | Per-tenant    | One per tenant; cached in `TenantRegistry`                       |
| `RiskService`                  | Per-tenant    | One per tenant                                                   |
| EventBus channels              | Per-tenant    | Keyed by `tenant_id`; one subscriber queue per WS connection     |
| Alpaca account                 | **Shared**    | One key pair in `.env`; per-tenant balances are virtual          |
| AlpacaMarketWS subscription    | **Shared**    | Union of every tenant's symbol; one socket for everyone          |
| OrderQueue                     | **Shared**    | One async queue with `tenant_id` on every job                    |
| ExecutionWorker                | **Shared**    | One worker, scopes its DB writes by `job.tenant_id`              |
| `candles` table                | **Shared**    | Market data is universal, deduped on (symbol, interval, open_time) |
| `sentiment_snapshots`          | **Shared**    | Sentiment is a market signal, not a user preference              |
| Anthropic client               | **Shared**    | One API key in `.env`                                            |

When a tenant signs up:

1. First API call → `current_tenant` lazy-creates a `User` and `Tenant` row.
2. UI calls `GET /api/bot/config` → `_get_or_create_config` creates a default `BotConfig` (BTC/USD, 30m, PAPER, $10k virtual).
3. User clicks Start → `start_session` creates `TradingSession`, `tenant_registry.ensure` constructs their `TradingCycleService`, the WS adds their symbol, the seed runs.
4. `TickScheduler` picks them up on the next 60s tick. Decision cycles fire on their interval.

Adding a second tenant repeats the whole flow independently. The two run
simultaneously because TickScheduler fans out cycles concurrently with a
per-process semaphore (`MAX_CONCURRENT_CYCLES = 5`). One slow cycle can't
starve the others.

## 15. Authentication

### JWT verification

`auth/clerk.py::verify_token`. Lazy-init's a `PyJWKClient` against
`settings.clerk_jwks_url`, which caches Clerk's signing keys in memory. On
every request:

```python
signing_key = jwk_client.get_signing_key_from_jwt(token).key
claims = jwt.decode(token, signing_key, algorithms=["RS256"], issuer=...,
                    options={"verify_aud": False})
```

Clerk JWTs have no audience by default, so we skip that check. Issuer is
verified.

### Tenant resolution

`auth/dependencies.py::current_tenant` is the FastAPI dependency every
per-user route uses:

```python
def current_tenant(
    claims: Annotated[dict, Depends(current_user_claims)],
    db: Annotated[Session, Depends(get_db)],
):
    user = _get_or_create_user(db, claims["sub"], claims.get("email", ""))
    return _get_or_create_tenant(db, user.id)
```

Both helpers are **race-safe**. The browser fires several routes in
parallel on first sign-in (status, config, decisions, trades), each running
through `current_tenant`. Two concurrent handlers can both read "user
missing" and both try to INSERT — one wins, the other hits a UNIQUE
constraint. The helper catches `IntegrityError`, rolls back, re-queries,
and returns the winner's row.

### WebSocket auth

Browsers can't set `Authorization` headers on WebSocket connections, so
`/ws?token=<jwt>` accepts the JWT as a query parameter. Same `verify_token`
path, same tenant resolution, then the connection subscribes to the
EventBus channel for that tenant.

## 16. Real-time updates

### EventBus

In-process async pub/sub keyed by `tenant_id`. Each subscriber gets its own
`asyncio.Queue(maxsize=256)`. Producers (`TradingCycleService`,
`ExecutionWorker`, `OcoTriggerService`) call:

```python
state.event_bus.publish(tenant_id, {"type": "new_decision", "decision": {...}})
```

Full queues drop the event with a warning rather than blocking the
producer. Single-process only — for horizontal scale, swap for Redis
pub/sub with the same interface.

### WebSocket endpoint

`/ws?token=<jwt>` subscribes to the tenant's channel and pumps events out
as JSON. The frontend `useTenantWS` hook opens one socket per page,
receives `TenantEvent` union messages (`new_decision`, `trade_opened`,
`trade_closed`, `price_tick`), and dispatches via callback.

## 17. Frontend architecture

### Routing

Two route groups:
- `app/(auth)/` — sign-in, sign-up. Public.
- `app/(app)/` — overview, settings, trades, account. Behind Clerk middleware.

`middleware.ts` runs `clerkMiddleware` on every route except `/sign-in*` and
`/sign-up*`, redirecting unauthenticated users to sign in.

### Data fetching

Every component uses `useApi<T>` (SWR-backed) with the Clerk JWT
auto-attached:

```tsx
const { data, mutate } = useApi<BotConfig>("/api/bot/config", { refreshInterval: 5000 });
```

Mutations go through `useApiMutate`:

```tsx
const { call } = useApiMutate();
await call<BotConfig>("/api/bot/config", "PUT", patch);
```

**Components never call `fetch` directly.** That keeps JWT handling,
error formatting, and base-URL resolution in one place.

### Date handling

`lib/format.ts::parseBackendDate` is required for every backend timestamp.
SQLite strips tzinfo on read despite `DateTime(timezone=True)`, so
sometimes the API emits naive ISO strings. `parseBackendDate` appends `Z`
when no tz suffix is present, ensuring `new Date()` interprets it as UTC
instead of the user's local time. The backend additionally calls
`util.dates.iso_utc()` on every datetime before serialising — belt and
braces.

### State

No Redux, no Zustand. SWR's cache + React state is enough for the v1
scope. The WebSocket pushes updates into local component state via the
hook callback; SWR pollers handle the rest.

## 18. Configuration: settings vs BotConfig

There are two layers of config, and the distinction matters:

**`app/config.py::settings`** — loaded once at startup from `.env`,
**immutable** for the life of the process. Holds:
- `anthropic_api_key`
- `clerk_jwks_url`, `clerk_issuer`, `clerk_webhook_secret`
- `database_url`
- `alpaca_api_key`, `alpaca_api_secret`, `alpaca_paper` (global safety flag)
- `paper_trading`, `default_paper_balance`, `taker_fee_rate` (defaults for new BotConfigs)

**`BotConfig` rows** — per-tenant runtime config, mutated via
`PUT /api/bot/config`. Holds:
- `symbol`, `interval`, `mode`
- `position_size_pct`, `min_confidence`, `stop_loss_pct`, `take_profit_pct`, `max_session_loss_pct`
- `use_sentiment`, `use_ai`
- `paper_balance_usd` (per-session virtual starting balance)
- `model_name` (Claude model id)
- `running`, `next_run_at` — runtime state owned by `session_service` + `TickScheduler`

While a session is **active**, every BotConfig field except `model_name`
is locked (the route layer drops them silently from the patch). This
prevents a user from changing position size mid-trade. Model name is
intentionally editable so you can hot-swap to a stronger model on the
next cycle without restarting.

## 19. Lifecycle: cold start to graceful shutdown

### Startup (`main.py::lifespan`)

```
1. init_db()
   - Imports all models (registers them on Base.metadata)
   - If RESET_DB_ON_STARTUP=true (default), drop all tables
   - create_all()

2. Build shared-plane singletons:
   - state.decision_provider = ClaudeDecisionProvider()
   - state.order_queue = OrderQueue()
   - state.execution_worker = ExecutionWorker(state.order_queue)
   - state.tenant_registry = TenantRegistry(decision_provider, order_queue)
   - state.tick_scheduler = TickScheduler(tenant_registry)
   - state.market_ws = AlpacaMarketWS(market_store, event_bus)
   - state.oco_trigger = OcoTriggerService(market_store, order_queue)
   - state.sentiment_refresher = SentimentRefresher(tenant_registry)

3. Restore tenants from any sessions that were RUNNING at last shutdown:
   for cfg in BotConfig.running == True:
       state.tenant_registry.ensure(cfg.tenant_id, cfg)

4. Seed market WS subscription from restored tenants.

5. Start the five long-running tasks:
   - state.execution_worker.start()
   - state.market_ws.start()
   - state.oco_trigger.start()
   - state.sentiment_refresher.start()
   - state.scheduler.add_job("tick", interval=60s)
   - state.scheduler.start()

6. logger.info("v2 API started")
7. yield  # ← app runs forever from here

(on shutdown)
8. state.scheduler.shutdown(wait=False)
9. await each task's .stop()
10. logger.info("v2 API stopped")
```

### Per-cycle (TickScheduler.tick)

Already covered in §6.

### Per-request (any HTTP route)

```
1. Clerk middleware (frontend) ensures the user is signed in
2. FastAPI dispatches to the route handler
3. Depends(current_tenant) verifies JWT + resolves tenant (lazy-creates if needed)
4. Depends(get_db) yields a SessionLocal scoped to this request
5. Handler logic runs
6. db is closed, response returned
```

### POST /api/bot/start specifically

1. Validates Alpaca keys configured.
2. **Synchronously seeds** candles + all 4 sentiment sources (≤ 20s timeout).
3. Calls `session_service.start_session` → creates RUNNING TradingSession, sets `next_run_at = now`, EquitySnapshot.
4. Calls `tenant_registry.ensure` to register the tenant.
5. Updates AlpacaMarketWS subscription, reconnects.
6. Returns `{ok, session_id, status}`.

By the time the response goes back, candles + sentiment are in the DB and
the next `TickScheduler.tick` (within 60s) runs the first cycle with full
data — the original "first decision is NULL" bootstrap problem is solved.

### POST /api/bot/stop

1. Calls `session_service.stop_session`:
   - Flips `BotConfig.running = false`, `next_run_at = None`.
   - Sets session.status = `STOPPING`.
   - Enqueues `CANCEL_OPEN_BUY_ORDERS`.
   - If a position is open, sets status = `EXITING` and enqueues `EXIT_POSITION_MARKET`.
   - Otherwise calls `end_session` directly.
2. ExecutionWorker handles the queued jobs asynchronously.
3. After the exit fill, `finalise_if_done` checks for remaining open positions / orders and either calls `end_session` or stays in `EXITING`.

## 20. Testing strategy

`api/tests/` — 96 tests, ~2.5s runtime. `pytest.ini` sets
`asyncio_mode = auto` so plain `async def test_*` functions work without
explicit markers.

### Fixtures (`tests/conftest.py`)

- `db` — in-memory SQLite with all tables created from `Base.metadata`.
- `tenant`, `make_tenant` — User+Tenant factory.
- `bot_config` — default-config row tied to `tenant`.
- `running_session` — TradingSession in RUNNING status.
- `seeded_candles` — 30 30m candles in a clear uptrend for BTC/USD.
- `session_factory` — a no-close proxy (`_NoCloseSession`) that
  intercepts handler-side `db.close()` calls so the test session stays
  alive for assertions. Used in execution-worker and oco-trigger tests
  where production code does `try/finally: db.close()`.

### Coverage by service

- **risk_service** — 10 tests covering every gate (kill switch, session loss cap, position state rules, confidence threshold, SL/TP clamping, balance, kill-switch precedence, HOLD short-circuit).
- **session_service** — 10 tests covering the state machine (start, idempotency, get_active, end_session pnl, end idempotency, finalise_if_done, mark_error, async stop with/without position).
- **claude_provider** — 12 tests covering JSON extraction, validation rules, position-state guards, mocked API responses (empty, garbage, happy path).
- **execution_worker** — 10 tests with mocked Alpaca: BUY/SELL fill paths, OCO leg recording, cancel-open-buys, status guards, fill-helper edge cases.
- **oco_trigger** — 4 tests covering SL fires below price, TP fires above price, no-trigger when in-range, no-trigger when no price.
- **sentiment_service** — 6 tests covering weighted blend, recency decay, GLOBAL → per-symbol blend, normalised headline, source breakdown, no-data path.
- **technical_service** — 6 tests on EMA/RSI/MACD with synthetic series, plus an integration test against seeded candles.
- **market_context_service** — 4 unit tests on HOLD compression, plus 2 integration tests covering build() + attach_decision_id().
- **order_queue** — 5 tests covering put/get, threadsafe, type validation, default payload, qsize.
- **position_service** — 4 tests on open/close winners/losers + unrealised math.
- **market_data_service** — 4 tests on interval helpers + refresh idempotency.
- **data_feeds** — 4 tests on symbol normalisation + timeframe mapping.
- **tenant_registry** — 5 tests on ensure idempotency + symbol union + unregister.
- **auth_dependencies** — 4 tests covering lazy create, idempotency, and the integrity-race re-query path.
- **util/dates** — 3 tests on naive/aware/None.
- **bot_control_seed** — 3 tests on the synchronous seed (candles + 4 sources, error swallowing).

### What's intentionally not tested

- `alpaca_ws.py` — pure WebSocket I/O; would need a mock server to test usefully.
- `tick_scheduler.py` — orchestrates services already covered individually.
- `auth/clerk.py` — wraps `pyjwt`; integration-level concerns.
- Full HTTP routes — would need Clerk JWT mocking; underlying services are covered.

### Running tests

```bash
cd v2/api
pytest                                     # full suite
pytest tests/test_risk_service.py          # one file
pytest tests/test_risk_service.py::test_kill_switch_forces_hold  # single
pytest -v                                  # verbose
pytest -x                                  # stop on first failure
pytest --tb=short                          # shorter tracebacks
```

## 21. Error handling philosophy

The system runs autonomously, so the goal is **degrade gracefully**, not
stop-the-world. Common patterns:

- **External API failures** (Alpaca, alternative.me, RSS feeds): log warning, return None, skip the source. The blend keeps working with whatever data is available.
- **Sentiment refresher tick fails**: outer try/except in `_run` catches it, logs, sleeps, tries again. The refresher never dies.
- **Decision provider fails**: cycle exits cleanly, no BotDecision row written, no order enqueued. Next cycle (in 30/60/240 minutes) tries again.
- **Risk service rejects**: BotDecision still gets written with `approved=false` and `rejection_reason` filled in for audit. No order.
- **Order placement fails at Alpaca**: `_submit_market_order` catches `APIError`, logs, returns None. The caller skips the rest of the handler. For exit orders this is severe — `mark_error` flips the session to ERROR so the UI surfaces it.
- **DB UNIQUE conflict on user/tenant lazy create**: caught, rollback, re-query. Wins the race transparently.
- **Frontend datetime parsing**: `parseBackendDate` defends against tz-naive strings even though the backend should never emit them.

The only thing that **stops** a tenant is `mark_error`, which sets
`session.status = ERROR` and surfaces it on the dashboard. The user can
review what happened and decide whether to start a new session.

## 22. Trade-offs and limitations

### Known weaknesses

- **Confidence is uncalibrated.** Claude's self-reported `0.7` doesn't mean "70% of these trades will be winners." There's no historical calibration loop yet.
- **Position size is binary.** Either you pass `min_confidence` and trade full `position_size_pct`, or you HOLD. A continuous size-weighting (e.g. `actual_size = config.size × (confidence − min) / (1 − min)`) would be a cheap improvement.
- **All sources weighted equally inside `weighted_score` save for source-quality.** A sudden RSS spike can cancel a strong price-action signal. Eventually we'd track per-source historical predictiveness and adjust weights from data.
- **Sentiment decay is symmetric.** A `+0.5` and a `-0.5` are treated as equally informative. In reality, fear-driven moves often have stronger short-term effects than equivalent positive news.
- **OCO trigger is client-side** for crypto. 2-second polling means firing latency is up to 2s — fine for 30m+ cycles with stops 2-5% away, not adequate for tight scalping. Switching to a broker that offers server-side bracket orders for crypto (Kraken, Bybit) would solve this.
- **One Alpaca account for all tenants.** Per-tenant balance isolation is virtual. If you actually had concurrent live tenants, their orders would collide on the shared account's positions. Per-tenant API keys (OAuth flow) is a v3 fix.
- **Single-process EventBus.** Horizontally scaling the API would require swapping to Redis pub/sub.
- **No Alembic migrations yet.** Dev DB resets on startup. Before going live or onboarding multiple paying users, the schema needs to lock in and Alembic needs to come online.
- **First cycle has a 60-second window.** The synchronous seed populates data before the session goes RUNNING, but the *first cycle itself* still fires within 60s on the next TickScheduler beat. Could be tightened by triggering the first cycle directly at the end of `start`.

### Why this design and not [other thing]

- **Why split the decision loop from the execution loop?** Reproducibility. A `BotDecision` + `MarketContextSnapshot` row pair is a complete record of "what the AI saw and decided." Replay is just "load these two and run them through the prompt builder." If decisions and orders were tangled, replay would mean moving money.
- **Why one OrderQueue + one ExecutionWorker?** Single source of truth for fills. No way for two concurrent handlers to double-place an order. If throughput becomes a concern, we add more workers reading from the same queue — the `tenant_id` on each job stays correct.
- **Why a 60s TickScheduler instead of per-tenant timers?** APScheduler one job = one ticker. Per-tenant timers would be N tickers we'd have to manage individually. Polling once per minute is cheap and crash-recoverable (`next_run_at` is in the DB).
- **Why VADER instead of an LLM as sentiment scorer?** Cost. Each cycle would need an extra Claude call to score headlines, tripling the per-cycle token spend. VADER + crypto lexicon is "good enough" for free. An LLM-based scorer is a v2.x option once cost analysis says it's worth it.
- **Why client-side OCO instead of server-side?** Alpaca crypto doesn't offer it. The whole project is currently locked to Alpaca because the user is in the UK and the next best alternative (Kraken) doesn't have a paper-trading sandbox for spot. The OCO service exists because of that constraint, and would be deleted on a broker swap.
- **Why SQLite?** Dev simplicity. Schema is portable; production swap to Postgres is an `engine = create_engine(postgres_url)` change plus Alembic migrations.

---

This document reflects the system as of late April 2026. Significant
architectural changes should be reflected here at the same time as the
code change — `CLAUDE.md` covers operational guidance and gotchas;
`README.md` covers quick-start. This file is the deep dive.
