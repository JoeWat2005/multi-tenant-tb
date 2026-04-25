// Session-aware Start/Stop control. The button adapts to the current
// session lifecycle:
//   IDLE / ENDED / ERROR → "Start" (primary)
//   STARTING             → disabled spinner
//   RUNNING              → "Stop" (destructive)
//   STOPPING / EXITING   → disabled "Stopping…" spinner
//
// Polls /api/bot/status every 2s so status transitions show up quickly
// after a click.
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Play, Square, Loader2, AlertCircle } from "lucide-react";
import { useApi, useApiMutate } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { BotStatus, SessionStatus } from "@/lib/api";

const STATUS_COPY: Record<SessionStatus, string> = {
  IDLE: "Idle",
  STARTING: "Starting",
  RUNNING: "Running",
  STOPPING: "Stopping",
  EXITING: "Exiting",
  ENDED: "Ended",
  ERROR: "Error",
};

const STATUS_TONE: Record<SessionStatus, string> = {
  IDLE: "bg-muted text-muted-foreground",
  STARTING: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  RUNNING: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  STOPPING: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  EXITING: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  ENDED: "bg-muted text-muted-foreground",
  ERROR: "bg-rose-500/15 text-rose-400 border-rose-500/30",
};

export function BotControls() {
  const { data, mutate } = useApi<BotStatus>("/api/bot/status", { refreshInterval: 2000 });
  const { call } = useApiMutate();
  const [busy, setBusy] = useState(false);

  const status: SessionStatus = data?.session_status ?? "IDLE";
  const isRunning = status === "RUNNING";
  const isTransitioning = status === "STARTING" || status === "STOPPING" || status === "EXITING";

  const toggle = async () => {
    if (!data || isTransitioning) return;
    setBusy(true);
    try {
      await call(isRunning ? "/api/bot/stop" : "/api/bot/start", "POST");
      await mutate();
    } finally {
      setBusy(false);
    }
  };

  const spinner = busy || isTransitioning;
  const buttonLabel = isRunning ? "Stop bot" : status === "STOPPING" || status === "EXITING"
    ? "Stopping…" : status === "STARTING" ? "Starting…" : "Start bot";

  return (
    <div className="flex items-center gap-3">
      {data?.mode && (
        <Badge variant="outline" className="h-7 px-2 text-[11px] font-mono uppercase tracking-wider">
          {data.mode}
        </Badge>
      )}
      <Badge
        className={cn("h-7 px-3 text-xs font-medium border", STATUS_TONE[status])}
        variant="outline"
      >
        <span className="inline-flex items-center gap-1.5">
          {status === "ERROR" ? <AlertCircle className="size-3" /> : (
            <span className={cn(
              "inline-block size-1.5 rounded-full",
              isRunning ? "bg-emerald-400"
                : isTransitioning ? "bg-amber-400 animate-pulse"
                : "bg-muted-foreground/60",
            )} />
          )}
          {STATUS_COPY[status]}
        </span>
      </Badge>
      <Button
        size="sm"
        onClick={toggle}
        disabled={spinner || !data}
        variant={isRunning ? "destructive" : "default"}
      >
        {spinner ? <Loader2 className="animate-spin" /> : isRunning ? <Square /> : <Play />}
        {buttonLabel}
      </Button>
    </div>
  );
}
