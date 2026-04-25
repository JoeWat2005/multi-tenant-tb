// Session performance summary — derived from the current session's Trade
// rows. A "trade" in this schema is a single fill; a round-trip position is
// an entry fill + an exit fill, so win rate / worst trade are computed by
// pairing BUYs with subsequent SELLs on the same position_id.
"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/hooks/use-api";
import { fmtUSD, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Trade } from "@/lib/api";

type Roundtrip = {
  position_id: number;
  entry: Trade;
  exit: Trade;
  pnl_usd: number;
  pnl_pct: number;
};

function pair(trades: Trade[]): Roundtrip[] {
  const byPos = new Map<number, { entry?: Trade; exit?: Trade }>();
  for (const t of trades) {
    if (t.position_id == null) continue;
    const rec = byPos.get(t.position_id) ?? {};
    if (t.side === "BUY") rec.entry = t;
    else if (t.side === "SELL") rec.exit = t;
    byPos.set(t.position_id, rec);
  }
  const out: Roundtrip[] = [];
  for (const [position_id, { entry, exit }] of byPos) {
    if (!entry || !exit) continue;
    const gross = (exit.price - entry.price) * entry.quantity;
    const fees = (entry.fee ?? 0) + (exit.fee ?? 0);
    const pnl = gross - fees;
    const cost = entry.price * entry.quantity;
    out.push({
      position_id, entry, exit,
      pnl_usd: pnl,
      pnl_pct: cost > 0 ? (pnl / cost) * 100 : 0,
    });
  }
  return out;
}

export function SessionPerformance() {
  const { data } = useApi<Trade[]>("/api/trades?limit=500", { refreshInterval: 5000 });
  const fills = data ?? [];
  const rts = pair(fills);

  const total = rts.length;
  const wins = rts.filter((r) => r.pnl_usd > 0).length;
  const winRate = total > 0 ? (wins / total) * 100 : null;
  const realised = rts.reduce((s, r) => s + r.pnl_usd, 0);
  const worst = rts.length ? rts.reduce((a, b) => (a.pnl_usd < b.pnl_usd ? a : b)) : null;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>Session performance</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
          <Metric label="Total trades" value={total > 0 ? String(total) : "—"} />
          <Metric
            label="Win rate"
            value={winRate != null ? `${winRate.toFixed(0)}%` : "—"}
            tone={winRate == null ? "" : winRate >= 50 ? "text-emerald-400" : "text-rose-400"}
          />
          <Metric
            label="Realised P&L"
            value={realised ? fmtUSD(realised) : "—"}
            tone={realised > 0 ? "text-emerald-400" : realised < 0 ? "text-rose-400" : ""}
          />
          <Metric
            label="Worst trade"
            value={worst ? fmtPct(worst.pnl_pct, { signed: true }) : "—"}
            tone={worst ? "text-rose-400" : ""}
          />
        </div>
        <div className="text-xs text-muted-foreground mt-3">
          {fills.length} fill{fills.length === 1 ? "" : "s"} · {rts.length} closed round-trip{rts.length === 1 ? "" : "s"}
        </div>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-lg font-semibold tabular-nums", tone)}>{value}</div>
    </div>
  );
}
