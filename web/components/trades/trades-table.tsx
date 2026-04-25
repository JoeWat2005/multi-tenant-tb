// Fill log for the current session. Each row is a single BUY or SELL
// execution — the round-trip view lives on the Session overview page.
"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useApi } from "@/hooks/use-api";
import { fmtUSD, fmtRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Trade } from "@/lib/api";

type Filter = "all" | "BUY" | "SELL";

export function TradesTable() {
  const [filter, setFilter] = useState<Filter>("all");
  const { data, isLoading } = useApi<Trade[]>("/api/trades?limit=200", {
    refreshInterval: 5000,
  });

  const rows = (data ?? []).filter((t) => filter === "all" || t.side === filter);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle>Fills</CardTitle>
        <div className="flex gap-1">
          {(["all", "BUY", "SELL"] as Filter[]).map((f) => (
            <Button
              key={f}
              size="sm"
              variant={filter === f ? "default" : "outline"}
              onClick={() => setFilter(f)}
            >
              {f}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="text-sm text-muted-foreground py-8 text-center">Loading…</div>
        )}
        {!isLoading && rows.length === 0 && (
          <div className="text-sm text-muted-foreground py-8 text-center">
            No fills in this session.
          </div>
        )}
        {!isLoading && rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-muted-foreground">
                <tr className="text-left border-b">
                  <th className="py-2 pr-3">Side</th>
                  <th className="py-2 pr-3">Symbol</th>
                  <th className="py-2 pr-3">Type</th>
                  <th className="py-2 pr-3 text-right">Price</th>
                  <th className="py-2 pr-3 text-right">Quantity</th>
                  <th className="py-2 pr-3 text-right">Notional</th>
                  <th className="py-2 pr-3 text-right">Fee</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 text-right">Filled</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => {
                  const notional = t.price * t.quantity;
                  return (
                    <tr key={t.id} className="border-b last:border-b-0 hover:bg-accent/40">
                      <td className="py-2 pr-3">
                        <Badge
                          variant="outline"
                          className={cn(
                            "font-semibold",
                            t.side === "BUY"
                              ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
                              : "text-rose-400 border-rose-500/30 bg-rose-500/10",
                          )}
                        >
                          {t.side}
                        </Badge>
                      </td>
                      <td className="py-2 pr-3 font-medium">{t.symbol}</td>
                      <td className="py-2 pr-3 text-muted-foreground text-xs">{t.order_type}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{fmtUSD(t.price)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{t.quantity.toFixed(6)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{fmtUSD(notional)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-muted-foreground">{fmtUSD(t.fee)}</td>
                      <td className="py-2 pr-3 text-muted-foreground text-xs">{t.status}</td>
                      <td className="py-2 text-right text-muted-foreground tabular-nums">
                        {fmtRelativeTime(t.filled_at ?? t.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
