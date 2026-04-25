# v2 Web

Next.js 15 (app router) + Clerk + shadcn/ui. Talks to `v2/api` via JWT
bearer auth (see `lib/api.ts`).

## Run

```bash
cd v2/web
npm install
cp .env.local.example .env.local   # fill in CLERK_* keys + NEXT_PUBLIC_API_URL
npm run dev -- -p 3001
```

## Screens (v1 scope)

- `/overview` — bot status, open positions, decisions feed (centerpiece)
- `/symbol/[symbol]` — candlestick chart + per-symbol decisions
- `/trades` — closed trade history
- `/settings` — tenant's BotConfig editor
- `/account` — Clerk `<UserProfile/>`

Non-auth: `/sign-in`, `/sign-up`.

## Real-time

`hooks/use-tenant-ws.ts` opens a WebSocket to `${NEXT_PUBLIC_API_URL}/ws?token=<jwt>`
and dispatches events to React state (new decisions, trade open/close, price ticks).
