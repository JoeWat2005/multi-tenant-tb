# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Claude Trader **v2** — a multi-tenant rebuild of the single-tenant v1 at `../crypto_bot/` + `../dashboard/`. v1 remains runnable while v2 is built; once milestone 4 (chart + trade history) ships, `v2/api` and `v2/web` are promoted to the top level and v1 is deleted.

Three top-level folders:

- `api/` — FastAPI backend (Python 3.13, SQLAlchemy, APScheduler, Clerk JWT auth, Alpaca for execution + crypto data, VADER for headline scoring)
- `web/` — Next.js 15 app router + Clerk + shadcn/ui + SWR
- `db/` — placeholder for Alembic migrations once the schema stabilises

## Commands

### API (`v2/api/`)

```bash
# First-time setup
python -m venv env
source env/Scripts/activate                # Windows bash
pip install -r requirements.txt
cp .env.example .env                       # fill ANTHROPIC_API_KEY, CLERK_*, ALPACA_API_KEY/SECRET

# Run (port 8001 — v1 lives on 8000)
uvicorn app.main:app --reload --port 8001

# Tests (93 tests in tests/, all stub external services)
pytest                                     # full suite, ~2.5s
pytest tests/test_risk_service.py          # one file
pytest tests/test_risk_service.py::test_kill_switch_forces_hold  # single test
pytest -v                                  # verbose
```

Dev DB resets on every startup because `RESET_DB_ON_STARTUP=true` is the default — set it to `false` in `.env` to preserve sessions/history across restarts. Schema changes during dev are picked up by the wipe-and-recreate.

### Web (`v2/web/`)

```bash
npm install
cp .env.local.example .env.local           # fill CLERK_* + NEXT_PUBLIC_API_URL=http://localhost:8001

npm run dev -- -p 3001                     # port 3001 — v1 lives on 3000
npm run build
npm run lint
npm run typecheck                          # tsc --noEmit
```

### Ports

| Service  | v1   | v2   |
|----------|------|------|
| FastAPI  | 8000 | 8001 |
| Next.js  | 3000 | 3001 |

## Architecture

### Backend: two planes

The API is split so shared market data is fetched once and per-tenant state is isolated.

**Shared plane** — one instance per process, built in `app/main.py` `lifespan` and stored on `app.state`:
- `AlpacaMarketWS` — single WS to Alpaca's crypto stream (`wss://stream.data.alpaca.markets/v1beta3/crypto/us`); subscription = union of every running tenant's symbol; auths once, re-subscribes on `reconnect()`
- `MarketStateStore` — in-memory last-known prices keyed by symbol (fed by the WS; read by everything that needs a "now" price without hitting REST)
- `ClaudeDecisionProvider` — wraps Anthropic SDK; consumes `MarketContext` directly and returns one `Decision`
- `SentimentRefresher` — async task on a 15-min cadence; per-source `MIN_GAP` self-skips
- `OrderQueue` + `ExecutionWorker` — single async queue, one consumer; every fill in the system goes through here, submitting real orders to Alpaca paper/live
- `OcoTriggerService` — client-side SL/TP watcher (Alpaca crypto has no server-side brackets); runs for both PAPER and LIVE
- `TenantRegistry` — lazy map of `tenant_id → TradingCycleService`
- `TickScheduler` — APScheduler 60s heartbeat; scans `bot_configs.running=true AND next_run_at <= now`, fans out cycles bounded by `MAX_CONCURRENT_CYCLES = 5`

**Per-tenant plane** — constructed on demand by `TenantRegistry.ensure(tenant_id, config)`:
- `TradingCycleService` (`services/tenant/trading_cycle.py`)
- `RiskService` (`services/tenant/risk_service.py`)

`session_service` and `position_service` are module-level functions that take `db` + `tenant_id` explicitly — state lives in the DB, not in memory.

### Decision pipeline

```
TickScheduler.tick()
  → TenantRegistry.ensure()
  → TradingCycleService.run(db, config)
      → market_data_service.refresh()           (persist closed candles from Alpaca)
      → MarketContextService.build()            (write MarketContextSnapshot + TechnicalSnapshot,
                                                 return MarketContext object)
      → decision_provider.decide(ctx, ...)      (Claude → strict JSON)
      → RiskService.evaluate()                  (clamps SL/TP, may force HOLD)
      → write BotDecision row                   (approved or rejected)
      → MarketContextService.attach_decision_id (link snapshot back to BotDecision)
      → order_queue.put({PLACE_MARKET_BUY | EXIT_POSITION_MARKET})
                                   │
                                   ▼
                       ExecutionWorker.run()
                          → submit Alpaca MarketOrderRequest
                          → write Trade + Position rows from Alpaca's fill response
                          → update session.current_balance (virtual)
                          → enqueue PLACE_OCO_SELL → two local Order rows for SL/TP
```

`OcoTriggerService` polls active SL/TP `Order` rows every 2s; when price crosses, it atomically flips one leg to `triggered` (single UPDATE WHERE status='active') and enqueues `EXIT_POSITION_MARKET`.

**Never** call `ExecutionWorker` methods directly from a route or cycle — always enqueue via `OrderQueue.put()` (or `put_threadsafe` from a non-async handler thread). Job type constants in `services/orchestration/order_queue.py`.

### MarketContext + AI prompt

`MarketContextService.build()` packages everything the AI sees into one object and persists a replay-able snapshot:

- **Sentiment**: weighted score (formula below), per-source breakdown (latest snapshot per source with weight + age), top-N deduped news headlines, raw F&G value
- **Market**: last 20 closed OHLCV candles, technical indicators (EMA9/21, RSI14, MACD/signal, vol/price change pct, trend label)
- **State**: open position, session P&L, current virtual balance
- **Memory**: last 5 previous decisions (with HOLD compression — see below), last 3 completed trades

The prompt sends **raw data**, not summarised labels — Claude sees actual numbers and headlines, not "sentiment is bullish."

**HOLD compression**: if the last 3+ decisions are all HOLD with similar reasons (first 6 words match), they collapse into one summary line in the prompt to avoid pollution. Code in `MarketContextService._compress_holds`.

**Decision JSON shape** (strict):
```json
{ "action": "BUY|SELL|HOLD", "confidence": 0.0,
  "reason": "...", "stopLossPct": 2, "takeProfitPct": 4 }
```
The AI suggests SL/TP percentages; `RiskService` clamps them to `2× config.stop_loss_pct` and `3× config.take_profit_pct`. `size_pct` is no longer AI-controlled — it comes from `BotConfig.position_size_pct`.

### Sentiment system

Four sources, each writing into `sentiment_snapshots` with a `source_weight` baked in:

| Source | Weight | Cadence (`MIN_GAP`) | Notes |
|---|---|---|---|
| `alpaca_news` | 1.3 | 15 min | Curated per-symbol via Alpaca news API; highest signal-to-noise |
| `price_action` | 1.2 | 15 min | Local momentum + range-position from candles |
| `fear_greed` | 0.8 | 60 min | alternative.me; updates ~daily upstream, dedup catches duplicates |
| `rss` | 0.7 | 15 min | 5 crypto-news feeds, keyword-filtered, VADER-scored |

VADER is patched with a crypto lexicon at module-load time (`pump`/`dump`/`rugpull`/`etf`/etc.) — see `CRYPTO_LEXICON` in `sentiment_service.py`.

**Weighted score formula** (`weighted_score`, default `half_life_minutes=90`, `lookback_hours=24`):
```
weight_i = exp(-age_min / half_life) * source_weight
score    = Σ(score_i * weight_i) / Σ(weight_i)
```

**Dedup**: each `_store()` computes `payload_hash` (SHA-256 of full JSON) and `normalized_hash` (SHA-256 of sorted unique normalised headlines) and skips writing if either matches the previous row from the same source. News fetchers also dedup articles in-memory by URL, then by `normalize_headline()` (lowercase, no punctuation, collapsed whitespace).

### Auth + tenant resolution

`app/auth/dependencies.py::current_tenant` is the gate for every per-user route:

1. `Authorization: Bearer <clerk_jwt>` → `verify_token` (JWKS-cached in `auth/clerk.py`)
2. Claims `sub` → `User` (lazy-created if Clerk webhook hasn't fired yet)
3. `User.id` → `Tenant` (also lazy-created)

Both lazy-creates are race-safe: catch `IntegrityError` on `flush()`, rollback, re-query — handles the case where the UI fires several routes in parallel on first login.

WebSocket routes accept the JWT via `?token=…` query param because browsers can't set `Authorization` on WS.

### Real-time push

`EventBus` (`app/event_bus.py`) is an in-process async pub/sub keyed by `tenant_id`. Producers: cycle service, execution worker, trigger executor. The per-user WebSocket endpoint subscribes to its own tenant's channel and forwards events as JSON. Single-process only — swap for Redis if scaling horizontally.

### Config vs settings

- `app/config.py::settings` is loaded once at startup from `.env` and treated as immutable (Anthropic key, Clerk, DB URL, Alpaca keys, kill switch).
- Per-tenant runtime config lives in `BotConfig` rows keyed by `tenant_id`. UI writes via `PUT /api/bot/config`; **every field except `model_name` is locked while a session is active** (see `bot_control.put_config`).

### Modes + Alpaca specifics

- `BotConfig.mode ∈ {PAPER, LIVE}`. PAPER → `paper-api.alpaca.markets`, LIVE → live endpoint.
- `ALPACA_PAPER=true` (env) is a global override that forces paper even when a tenant picks LIVE — belt-and-braces safety net.
- All tenants share **one Alpaca account** (single key pair in `.env`). Per-tenant balance isolation is **virtual**: `TradingSession.starting_balance` / `current_balance` are synthetic per-session pools; real Alpaca fills update those at the prices Alpaca reports. The Alpaca account cash drifts from `Σ(session balances)` — that's expected. Per-tenant API keys are a v3 concern.
- Allowed intervals: **30m, 1h, 4h** only. Validated at the route layer (`bot_control.BotConfigIn`).
- Symbol format: Alpaca crypto uses `BTC/USD` (with slash). `data_feeds.to_alpaca_symbol` normalises legacy `BTCUSDT` / `BTCUSD` inputs.
- `Alpaca-py` types: `MarketOrderRequest(notional=...)` for BUY (dollar amount); `MarketOrderRequest(qty=...)` for SELL. Never both at once.

### Frontend data layer

- `web/lib/api.ts` — response type definitions mirroring backend Pydantic schemas (hand-synced; replace with generated OpenAPI types eventually)
- `web/hooks/use-api.ts` — SWR + Clerk JWT in one place; **every feature hook is a thin wrapper around `useApi` / `useApiMutate`**. Don't call `fetch` directly in components.
- `web/hooks/use-tenant-ws.ts` — single WS connection per page; forwards `TenantEvent` union to a callback
- `web/middleware.ts` — Clerk middleware gating everything except `/sign-in` and `/sign-up`
- `web/lib/format.ts::parseBackendDate` — parses backend ISO strings as UTC when the tz suffix is missing (defends against SQLite stripping tzinfo). Use this everywhere you'd otherwise call `new Date(iso)`.
- Route groups: `app/(app)/…` is the signed-in shell, `app/(auth)/…` is sign-in/up

## Key files when extending

| Task | Start here |
|------|-----------|
| New API route | `api/app/api/routes/…` → register in `main.py`. Use `iso_utc(dt)` from `app.util.dates` for any datetime field |
| New order type | constant + `JOB_TYPES` in `order_queue.py`, handler branch in `execution_worker.py` |
| New sentiment source | `sentiment_service.refresh_<name>()` returning a `SentimentSnapshot` via `_store()`; add to `SOURCE_WEIGHTS` and `MIN_GAP`; wire into `SentimentRefresher._tick` |
| New AI input | extend `MarketContext` dataclass + the `MarketContextService.build()` body; add a section in `claude_provider._user_prompt()` |
| New technical indicator | add helper in `technical_service.py`, populate `TechnicalReading`; persist via the existing `TechnicalSnapshot` columns or extend the model |
| Schema change | add/modify model in `api/app/models/…` and import it in `models/__init__.py`. Dev DB recreates on startup; production needs Alembic eventually |
| New UI screen | `web/app/(app)/<name>/page.tsx` → data via `useApi`, mutations via `useApiMutate` |
| New real-time event | publish via `state.event_bus.publish(tenant_id, {...})`; extend `TenantEvent` union in `use-tenant-ws.ts` |
| New test | drop into `api/tests/`; reuse fixtures `db`, `tenant`, `bot_config`, `running_session`, `seeded_candles`, `session_factory`. Async tests work natively (asyncio_mode=auto) |

## Gotchas

- `app/state.py` singletons are `None` until `lifespan` runs — routes must null-check (e.g. `bot_control.start` returns 503 if `tenant_registry is None`).
- The Alpaca WS subscription set is the **union of all running tenants' symbols**. After changing a tenant's symbol or starting a session, call `state.market_ws.set_subscription(state.tenant_registry.union_of_tracked_symbols())` followed by `await state.market_ws.reconnect()` so the socket re-auths and re-subscribes.
- Each order job opens its own `SessionLocal()` — do not share DB sessions across the queue boundary.
- `BotDecision` rows are written for every cycle (approved or rejected). This is by design so we can replay a decision in tests without moving money.
- Intervals on `BotConfig.interval` use the short form (`30m`, `1h`, `4h`) and are translated to Alpaca `TimeFrame` in `data_feeds.to_alpaca_timeframe`. `TickScheduler` polls every 60s and uses `next_run_at`, so a `30m` interval fires every 30 ticks.
- Alpaca market-order fills are usually synchronous in paper mode (`filled_avg_price` set on the returned order), but live can be async. `ExecutionWorker._fill_price` falls back to the last WS price so accounting doesn't stall on a pending status.
- SQLite strips tzinfo on read despite `DateTime(timezone=True)`. Always emit datetimes via `app.util.dates.iso_utc(dt)` from API routes; the frontend has a matching `parseBackendDate` shim. Don't use raw `.isoformat()` or `new Date(iso)` for backend timestamps.
- `RiskService` enforces `stop_loss_pct ≤ 2× config` and `take_profit_pct ≤ 3× config` to bound the AI's suggestions. AI-suggested `size_pct` is ignored — config is source of truth.
- The first decision cycle after a fresh boot can run with no sentiment data (refresher's first tick is no-op when no tenants are registered yet, and the first cycle fires within 60s of `/start`). The next cycle 30 min later sees sentiment.
- Tests use a `_NoCloseSession` proxy so production handlers' `try/finally: db.close()` doesn't kill the fixture session. See `tests/conftest.py`.
- `v2/api/crypto_bot_v2.db` is a separate SQLite file from v1's DB — schemas diverge.
