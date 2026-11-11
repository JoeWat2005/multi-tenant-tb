> [!WARNING]
> **Deprecated: no longer maintained.** Last updated in April 2026, while still at an early stage; kept for reference.

# Claude Trader v2 (multi-tenant)

A multi-tenant crypto trading bot platform: each signed-in user runs their own bot session, in which Claude reviews the market on a schedule and approved trades are placed through Alpaca, on the paper-trading endpoint by default.

**Status:** deprecated at an early stage. This is a multi-tenant rebuild of an earlier single-tenant bot and dashboard, which are not part of this repository. There are no database migrations yet, and the development database is reset on every start by default.

For research and education only. This is not financial advice. Keep `ALPACA_PAPER=true` unless you intend to place real orders.

## Features

- Sign-up and sign-in with Clerk. Per-user API routes resolve the caller's tenant from the Clerk JWT, and a signed Clerk webhook keeps users and tenants in sync.
- Per-tenant bot settings: symbol (default `BTC/USD`), candle interval (`30m`, `1h` or `4h`), mode (`PAPER` or `LIVE`), position size, minimum confidence, stop-loss and take-profit, session loss cap, sentiment on or off, look-back length, paper balance and Claude model.
- A decision cycle per tenant: refresh candles, build a market context (sentiment, news, the Fear & Greed index, technical indicators, the open position and recent decisions), ask Claude for a strict-JSON verdict, apply risk checks, record the decision, and queue an order if it is approved.
- Risk gates, including a global kill switch, a session loss cap, position-state rules, a confidence threshold, stop-loss and take-profit clamping, and a virtual balance check.
- Sentiment blended from four weighted sources with recency decay.
- Stop-loss and take-profit (OCO) exits handled by the backend, because Alpaca does not offer server-side bracket orders for crypto.
- Next.js dashboard with an overview (bot controls, stats, session performance, equity chart, open position and decisions feed), trade history and settings, updated live over a WebSocket.

## Tech stack

- **Backend:** Python 3.13 (per `DOCUMENTATION.md`), FastAPI, SQLAlchemy 2, APScheduler, Pydantic Settings, the Anthropic SDK, alpaca-py, VADER sentiment (with crypto vocabulary), feedparser and PyJWT
- **Database:** SQLite for development (`crypto_bot_v2.db`)
- **Frontend:** Next.js 15 (App Router), React 19, TypeScript, Clerk, shadcn/ui, Tailwind CSS 4, SWR and lightweight-charts
- **Tests:** pytest and pytest-asyncio

## Getting started

### Prerequisites

- Python 3.13, the version given in `DOCUMENTATION.md`.
- Node.js and npm. The repository does not pin a version.
- An Anthropic API key, a Clerk application, and Alpaca API keys (a paper-trading account is enough).

### Installation

The Python requirements file is at the repository root:

```bash
python -m venv env
source env/Scripts/activate      # Windows (Git Bash); on macOS or Linux use env/bin/activate
pip install -r requirements.txt
```

Install the web app's packages:

```bash
cd web
npm install
```

### Configuration

Copy the two templates and fill them in:

```bash
cp api/.env.example api/.env
cp web/.env.local.example web/.env.local
```

API settings (`api/.env`, read by `app/config.py` from the directory where you start the server):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key for trading decisions |
| `CLERK_JWKS_URL` | Clerk JWKS endpoint, used to verify session tokens |
| `CLERK_ISSUER` | Expected issuer of Clerk tokens |
| `CLERK_WEBHOOK_SECRET` | Signing secret for the Clerk webhook at `/api/webhooks/clerk` |
| `DATABASE_URL` | SQLAlchemy database URL (a local SQLite file by default) |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | Alpaca keys for market data and orders |
| `ALPACA_PAPER` | Global safety flag: when true, tenants in `LIVE` mode are routed to the paper endpoint |
| `PAPER_TRADING`, `DEFAULT_PAPER_BALANCE`, `TAKER_FEE_RATE` | Declared in `app/config.py` but not used anywhere else in the current code |
| `RESET_DB_ON_STARTUP` | Drops and recreates every table on start-up (on by default) |
| `KILL_SWITCH` | When true, the risk service forces every decision to HOLD; read at evaluation time, so no restart is needed |

`RESET_DB_ON_STARTUP` and `KILL_SWITCH` are read with `os.getenv` rather than through the settings class, and nothing loads `api/.env` into the process environment. Set these two in the shell that starts the API; putting them in `api/.env` has no effect.

Web settings (`web/.env.local`):

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | Clerk keys for the Next.js app |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Sign-in and sign-up routes |
| `NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL`, `NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL` | Where users land after signing in or up |
| `NEXT_PUBLIC_API_URL` | Base URL of the FastAPI backend |

The web template sets `NEXT_PUBLIC_API_URL` to port 8000, but the commands below start the API on port 8001 (which the frontend code also falls back to when the variable is unset). Point it at the port you actually use.

### Run

Start the API from `api/`, so that `.env` and the SQLite file resolve there:

```bash
cd api
uvicorn app.main:app --reload --port 8001
```

Start the web app in a second terminal:

```bash
cd web
npm run dev -- -p 3001
```

Then open <http://localhost:3001>. The API accepts cross-origin requests from `http://localhost:3000` and `http://localhost:3001`. `web/package.json` also has `build`, `start`, `lint` and `typecheck` scripts.

### Tests

```bash
cd api
pytest                               # full suite
pytest tests/test_risk_service.py    # one file
pytest -v                            # verbose
```

According to `CLAUDE.md`, the tests stub all external services.

## How it works

The backend has two planes:

- **Shared plane** (process-wide singletons): one Alpaca crypto WebSocket subscribed to the union of every running tenant's symbol, one `OrderQueue` and `ExecutionWorker` for all fills, a `SentimentRefresher` on a 15-minute cadence, an `OcoTriggerService` that checks stop-loss and take-profit levels every 2 seconds, and a `TickScheduler` that runs every 60 seconds.
- **Per-tenant plane:** `TradingCycleService` and `RiskService`, created on demand by the `TenantRegistry` and keyed by `tenant_id`.

On each tick the scheduler finds tenants whose next run is due and runs their cycles:

1. Refresh closed candles from Alpaca.
2. Build a `MarketContext` (sentiment, news, Fear & Greed, technicals, open position and memory) and save a snapshot of it.
3. Ask Claude for a strict-JSON decision: action, confidence, reason, stop-loss and take-profit. The default model is `claude-sonnet-4-6`, stored per tenant.
4. Run the risk checks.
5. Save a `BotDecision` row, approved or not, with any rejection reason.
6. If the decision is approved and actionable, queue a job. The `ExecutionWorker` then submits the Alpaca order.

The cycle never places orders itself, so every decision and its saved context can be replayed in tests without moving money.

All tenants share one Alpaca account. Each `TradingSession` has its own virtual `starting_balance`, so balances are isolated only virtually; per-tenant API keys are listed as future work.

Sentiment sources:

| Source | Weight | Refresh |
|---|---|---|
| `alpaca_news` | 1.3 | 15 min |
| `price_action` | 1.2 | 15 min |
| `fear_greed` | 0.8 | 60 min |
| `rss` | 0.7 | 15 min |

They are blended as `score = Σ(score_i × recency_weight × source_weight) / Σ(recency_weight × source_weight)`, with a 90-minute half-life and a 24-hour look-back.

Key files:

- `api/app/main.py`: start-up, wiring of the shared services and the 60-second tick
- `api/app/services/tenant/trading_cycle.py`: the per-tenant decision cycle
- `api/app/services/tenant/risk_service.py`: risk gates
- `api/app/services/shared/claude_provider.py`: prompt building and the Claude call
- `api/app/services/orchestration/execution_worker.py`: order submission
- `api/app/auth/`: Clerk token verification and tenant resolution
- `web/hooks/use-tenant-ws.ts`: WebSocket client for live updates

[DOCUMENTATION.md](DOCUMENTATION.md) is the full system write-up, and [CLAUDE.md](CLAUDE.md) holds operational notes for AI coding agents.

### Known limitations

From `DOCUMENTATION.md`:

- Claude's confidence is uncalibrated, and position size is all-or-nothing once the confidence threshold is met.
- All tenants share one Alpaca account, so tenant isolation is virtual.
- Stop-loss and take-profit exits are polled by the backend every 2 seconds, which suits 30-minute and longer cycles but not tight scalping.
- The event bus runs in a single process, so scaling the API horizontally would need something like Redis pub/sub.
- There are no Alembic migrations, and the development database resets on start-up.

### Milestones

The original milestone list:

1. Auth handshake: Clerk JWT to FastAPI, one protected route (done)
2. Decisions feed live for a single tenant (done)
3. Hard tenant isolation with per-tenant risk and execution (done)
4. Chart and trade history ported from the earlier bot
5. Real-time WebSocket push of decisions and trades

Milestones 4 and 5 were not marked done when that list was last updated, although the `/api/chart`, `/api/trades` and `/ws` routes already exist.

## Project structure

```
.
├── api/                  # FastAPI backend
│   ├── app/
│   │   ├── api/routes/   # HTTP and WebSocket endpoints
│   │   ├── auth/         # Clerk token verification and tenant resolution
│   │   ├── db/           # engine, sessions and table creation
│   │   ├── models/       # SQLAlchemy models
│   │   ├── services/     # orchestration, shared-plane and per-tenant services
│   │   └── util/         # small helpers
│   ├── tests/            # pytest suite
│   └── .env.example      # API environment template
├── web/                  # Next.js frontend
│   ├── app/              # (auth) and (app) route groups
│   ├── components/       # overview, settings, trades, shared and UI components
│   ├── hooks/            # data fetching and WebSocket hooks
│   ├── lib/              # API types and formatting helpers
│   └── .env.local.example
├── requirements.txt      # Python dependencies for the API
├── DOCUMENTATION.md      # full system documentation
└── CLAUDE.md             # notes for AI coding agents
```

## Related repositories

- [quantforge](https://github.com/JoeWat2005/quantforge): local quantitative research lab with a Parquet price warehouse, walk-forward experiments and Monte Carlo simulation.
- [evolutionary-strategy-search](https://github.com/JoeWat2005/evolutionary-strategy-search): prototype that evolves indicator-based trading rules and backtests them.
