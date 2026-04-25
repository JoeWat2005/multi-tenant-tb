# Claude Trader — v2 (multi-tenant)

Parallel rebuild of `../crypto_bot/` + `../dashboard/` as a multi-tenant,
Clerk-authenticated platform. v1 remains runnable while v2 is built.

## Stack

- **Backend** — FastAPI, SQLAlchemy, APScheduler, Anthropic SDK, [alpaca-py](https://alpaca.markets/sdks/python/), VADER (crypto-tuned) for headline scoring
- **Frontend** — Next.js 15 (app router), Clerk for auth, shadcn/ui, SWR, lightweight-charts
- **DB** — SQLite for dev (`crypto_bot_v2.db`); schema reset on startup by default

## Layout

```
v2/
  api/    FastAPI backend
    app/
      api/routes/      HTTP + WebSocket endpoints
      auth/            Clerk JWT verification + tenant resolution
      models/          SQLAlchemy ORM models
      services/
        orchestration/   tenant_registry, tick_scheduler, order_queue,
                         execution_worker, oco_trigger
        shared/          alpaca_ws, market_state, market_data_service,
                         sentiment_service, technical_service,
                         market_context_service, claude_provider
        tenant/          trading_cycle, risk_service, session_service,
                         position_service
      util/            small helpers (e.g. UTC datetime serialisation)
    tests/             pytest suite (93 tests, ~2.5s)
  web/    Next.js 15 frontend
    app/    (auth) + (app) route groups
    components/
    hooks/             use-api, use-auth-fetch, use-tenant-ws
    lib/               api types, format helpers (incl. parseBackendDate)
  db/     placeholder for Alembic migrations
```

## Quick start

### API

```bash
cd v2/api
python -m venv env
source env/Scripts/activate                # Windows bash
pip install -r requirements.txt
cp .env.example .env                       # fill ANTHROPIC_API_KEY, CLERK_*, ALPACA_API_KEY/SECRET
uvicorn app.main:app --reload --port 8001
```

### Web

```bash
cd v2/web
npm install
cp .env.local.example .env.local           # fill CLERK_* + NEXT_PUBLIC_API_URL
npm run dev -- -p 3001
```

### Tests

```bash
cd v2/api
pytest                                     # full suite
pytest tests/test_risk_service.py          # one file
pytest -v                                  # verbose
```

## Run side-by-side with v1

| Service       | v1 port | v2 port |
|---------------|---------|---------|
| FastAPI       | 8000    | 8001    |
| Next.js       | 3000    | 3001    |

## Architecture overview

The backend is two-plane:

- **Shared plane** (process-wide singletons): one Alpaca crypto WebSocket
  subscribed to the union of every running tenant's symbol; one
  `OrderQueue` + `ExecutionWorker` for all fills; one `SentimentRefresher`
  on a 15-min cadence; one `TickScheduler` heartbeating every 60s.
- **Per-tenant plane**: `TradingCycleService` + `RiskService` constructed
  on demand by the `TenantRegistry`, keyed by `tenant_id`.

Each cycle does the same thing: refresh candles → build a
`MarketContext` (sentiment + news + F&G + technicals + position +
memory) → ask Claude for a strict-JSON verdict → risk-validate →
write a `BotDecision` row → enqueue an order if approved.

All tenants share **one Alpaca paper account**; per-tenant balance
isolation is virtual (each `TradingSession` has its own
`starting_balance`). Per-tenant API keys are a v3 concern.

See `CLAUDE.md` for the deep dive — decision pipeline, sentiment
weighting, schema, and gotchas.

## Trading config

Allowed intervals: **30m, 1h, 4h**. Allowed modes: **PAPER, LIVE**.
The global `ALPACA_PAPER=true` env flag overrides mode=LIVE back to
the paper endpoint as a safety net.

## Sentiment sources

| Source | Weight | Cadence |
|---|---|---|
| `alpaca_news` | 1.3 | 15 min |
| `price_action` | 1.2 | 15 min |
| `fear_greed` | 0.8 | 60 min |
| `rss` | 0.7 | 15 min |

Blended via `score = Σ(score_i × recency_weight × source_weight) / Σ(recency_weight × source_weight)`
with a 90-min half-life and 24-hour lookback.

## Milestones

1. Auth handshake — Clerk JWT → FastAPI, one protected route ✓
2. Decisions feed live for a single tenant ✓
3. Hard tenant isolation (per-tenant Risk + Execution) ✓
4. Chart + trade history ported from v1
5. Real-time WS push of decisions / trades

## Exit plan

Once milestone 4 ships, delete `crypto_bot/` + `dashboard/` and promote
`v2/api` → `api/`, `v2/web` → `web/`.
