// Top stat strip — four cards scoped to the current session.
//   1. Session P&L (USD + %)
//   2. Current balance (USD)
//   3. Session duration (ticks every second)
//   4. Session status (same pill as BotControls, compact)
"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { useApi } from "@/hooks/use-api";
import { fmtUSD, fmtPct, fmtDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { TradingSession } from "@/lib/api";

export function StatCards() {
  const { data } = useApi<{ session: TradingSession | null }>("/api/session/current", {
    refreshInterval: 3000,
  });
  const session = data?.session ?? null;

  // 1s tick so "Duration" updates live without refetching.
  const [, setNow] = useState(0);
  useEffect(() => {
    if (!session || session.status === "ENDED" || session.status === "ERROR") return;
    const t = setInterval(() => setNow((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [session?.status]);

  const pnl = session?.session_pnl ?? 0;
  const pnlPct = session?.session_pnl_pct ?? 0;
  const balance = session?.current_balance ?? session?.starting_balance ?? 0;
  const duration = session?.started_at
    ? fmtDuration(session.started_at, session.ended_at ?? null)
    : "—";

  const pnlTone =
    pnl > 0 ? "text-emerald-400" :
    pnl < 0 ? "text-rose-400" :
    "text-foreground";

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <StatCard label="Session P&L" accent={pnlTone}>
        <div className="text-xl font-semibold tabular-nums">{fmtUSD(pnl, { digits: 2 })}</div>
        <div className={cn("text-xs tabular-nums", pnlTone)}>{fmtPct(pnlPct, { signed: true })}</div>
      </StatCard>
      <StatCard label="Balance">
        <div className="text-xl font-semibold tabular-nums">{fmtUSD(balance)}</div>
        <div className="text-xs text-muted-foreground tabular-nums">
          Start {fmtUSD(session?.starting_balance ?? 0)}
        </div>
      </StatCard>
      <StatCard label="Duration">
        <div className="text-xl font-semibold tabular-nums">{duration}</div>
        <div className="text-xs text-muted-foreground">{session?.symbol ?? "—"} · {session?.interval ?? "—"}</div>
      </StatCard>
      <StatCard label="Status">
        <div className="text-xl font-semibold">{session?.status ?? "IDLE"}</div>
        <div className="text-xs text-muted-foreground">
          {session?.open_positions ? `${session.open_positions} open` : "No position"}
        </div>
      </StatCard>
    </div>
  );
}

function StatCard({
  label, children, accent,
}: { label: string; children: React.ReactNode; accent?: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs text-muted-foreground mb-1">{label}</div>
        <div className={cn(accent)}>{children}</div>
      </CardContent>
    </Card>
  );
}
