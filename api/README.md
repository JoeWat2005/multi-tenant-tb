# v2 API

FastAPI backend. Multi-tenant. Clerk for auth. Alpaca for order execution
and crypto market data.

## Architecture

Two planes:

- **Shared plane** (singletons): `alpaca_ws`, `market_state`,
  `sentiment_refresher`, `decision_provider`, `order_queue`,
  `execution_worker`, `oco_trigger`. One instance serves all tenants.

- **Per-tenant plane**: `risk_service`, `trading_cycle`. Constructed on
  demand, held in `tenant_registry`, keyed by `tenant_id`.

Orchestration (`tick_scheduler`) runs one job every 60s that scans `bot_configs`
for tenants whose `next_run_at <= now()` and fans out cycles bounded by a
semaphore.

Alpaca notes:
- PAPER mode → `paper-api.alpaca.markets` (Alpaca's built-in paper account).
- LIVE mode → live endpoint. The global `ALPACA_PAPER=true` flag in `.env`
  overrides any tenant's mode=LIVE back to paper as a safety net.
- Per-tenant virtual balance is tracked on `TradingSession.current_balance`
  (synthetic, isolated per tenant). Real orders settle against the shared
  Alpaca account, but each session's P&L is computed from its own
  `starting_balance` at fill prices Alpaca reports.
- Alpaca crypto does NOT support server-side bracket / OCO orders, so SL/TP
  legs are stored as local `Order` rows and `OcoTriggerService` scans them
  every 2s and enqueues `EXIT_POSITION_MARKET` when a level is hit.

## Run

```bash
cd v2/api
python -m venv env
source env/Scripts/activate
pip install -r requirements.txt
cp .env.example .env          # fill ANTHROPIC_API_KEY, CLERK_*, ALPACA_API_KEY/SECRET
uvicorn app.main:app --reload --port 8001
```

## Database

SQLite at `v2/api/crypto_bot_v2.db` (separate from v1's DB — schema diverges).
