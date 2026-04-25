// The only open position (single-ticker bot). Shows entry/current price,
// qty, position value, size %, SL/TP (with % from entry), live P&L, and
// how long the position has been open.
"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApi } from "@/hooks/use-api";
import { fmtUSD, fmtPct, fmtDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { OpenPosition } from "@/lib/api";

export function OpenPositionCard() {
  const { data } = useApi<OpenPosition[]>("/api/positions", { refreshInterval: 3000 });
  const pos = (data ?? [])[0];

  if (!pos) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle>Open position</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground py-6 text-center">
            No open position.
          </div>
        </CardContent>
      </Card>
    );
  }

  // Unrealised P&L is written to the position row; if zero, fall back to a
  // synthetic calc from entry vs any current_price we might pass later.
  const pnlUsd = pos.unrealised_pnl ?? 0;
  const pnlPct = pos.entry_price > 0 && pos.position_value > 0
    ? (pnlUsd / pos.position_value) * 100
    : 0;
  const pnlTone =
    pnlUsd > 0 ? "text-emerald-400" :
    pnlUsd < 0 ? "text-rose-400" :
    "text-muted-foreground";

  const slPct = pos.stop_loss_price != null && pos.entry_price > 0
    ? (pos.stop_loss_price - pos.entry_price) / pos.entry_price * 100
    : null;
  const tpPct = pos.take_profit_price != null && pos.entry_price > 0
    ? (pos.take_profit_price - pos.entry_price) / pos.entry_price * 100
    : null;

  return (
    <Card>
      <CardHeader className="pb-3 flex flex-row items-baseline justify-between">
        <CardTitle>{pos.symbol}</CardTitle>
        <div className={cn("text-sm font-semibold tabular-nums", pnlTone)}>
          {fmtUSD(pnlUsd)} <span className="text-xs">({fmtPct(pnlPct, { signed: true })})</span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <Row label="Entry" value={fmtUSD(pos.entry_price, { digits: 2 })} />
          <Row label="Quantity" value={pos.quantity.toFixed(6)} />
          <Row label="Notional" value={fmtUSD(pos.position_value)} />
          <Row label="Size" value={`${pos.size_pct.toFixed(1)}%`} />
          <Row
            label="Stop loss"
            value={
              <span className="space-x-1">
                <span className="tabular-nums">{fmtUSD(pos.stop_loss_price, { digits: 2 })}</span>
                {slPct != null && (
                  <span className="text-xs text-rose-400/80 tabular-nums">
                    {fmtPct(slPct, { signed: true })}
                  </span>
                )}
              </span>
            }
          />
          <Row
            label="Take profit"
            value={
              <span className="space-x-1">
                <span className="tabular-nums">{fmtUSD(pos.take_profit_price, { digits: 2 })}</span>
                {tpPct != null && (
                  <span className="text-xs text-emerald-400/80 tabular-nums">
                    {fmtPct(tpPct, { signed: true })}
                  </span>
                )}
              </span>
            }
          />
          <Row
            label="Open for"
            value={pos.opened_at ? fmtDuration(pos.opened_at, null) : "—"}
            span
          />
        </div>
      </CardContent>
    </Card>
  );
}

function Row({
  label, value, span = false,
}: { label: string; value: React.ReactNode; span?: boolean }) {
  return (
    <div className={cn("flex items-baseline justify-between", span && "col-span-2")}>
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  );
}
