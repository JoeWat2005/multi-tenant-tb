// Decisions feed — every AI verdict from the current session, newest first.
// Approved decisions show as-is; rejected ones dim out and surface the risk
// reason. Polls /api/decisions every 5s.
"use client";

import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ActionBadge } from "@/components/shared/action-badge";
import { useApi } from "@/hooks/use-api";
import { fmtRelativeTime, fmtUSD } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { BotDecision } from "@/lib/api";

export function DecisionsFeed() {
  const { data, isLoading } = useApi<BotDecision[]>(
    "/api/decisions?limit=50",
    { refreshInterval: 5000 },
  );

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>Decisions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {isLoading && (
          <>{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}</>
        )}
        {!isLoading && (data ?? []).length === 0 && (
          <div className="text-sm text-muted-foreground py-8 text-center">
            No decisions yet. Start the bot to see AI decisions appear here.
          </div>
        )}
        {(data ?? []).map((d) => (
          <div
            key={d.id}
            className={cn(
              "border rounded-md p-3 flex items-start gap-3 bg-card",
              !d.approved && "opacity-70",
            )}
          >
            <ActionBadge action={d.action} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-semibold">{d.symbol}</span>
                <span className="text-muted-foreground">
                  {(d.confidence * 100).toFixed(0)}%
                </span>
                {d.price_snapshot != null && (
                  <span className="text-muted-foreground tabular-nums">
                    · {fmtUSD(d.price_snapshot, { digits: 2 })}
                  </span>
                )}
                <span className={cn(
                  "ml-auto text-xs px-1.5 py-0.5 rounded border tabular-nums",
                  d.approved
                    ? "border-emerald-500/30 text-emerald-400/90 bg-emerald-500/10"
                    : "border-rose-500/30 text-rose-400/90 bg-rose-500/10",
                )}>
                  {d.approved ? "approved" : "rejected"}
                </span>
                <span className="text-muted-foreground text-xs tabular-nums">
                  {fmtRelativeTime(d.created_at)}
                </span>
              </div>
              <div className="text-sm text-muted-foreground mt-1">{d.reason || "—"}</div>
              {!d.approved && d.rejection_reason && (
                <div className="text-xs text-rose-400/80 mt-1">
                  Risk: {d.rejection_reason}
                </div>
              )}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
