// Opens a WebSocket to the backend and dispatches events.
// Token goes in the query string — browsers can't set Authorization on WS.
//
// Server pushes events of shape:
//   { type: "new_decision" | "trade_opened" | "trade_closed" | "price_tick", ... }
//
// TODO: wire this into a Zustand/Context store so multiple components
// can subscribe without each opening their own WS.
"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect, useRef } from "react";

type TenantEvent =
  | { type: "new_decision"; decision: unknown }
  | { type: "trade_opened"; trade: unknown }
  | { type: "trade_closed"; trade: unknown }
  | { type: "price_tick"; symbol: string; price: number };

export function useTenantWS(onEvent?: (event: TenantEvent) => void) {
  const { getToken } = useAuth();
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const token = await getToken();
      if (!token || cancelled) return;

      const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
      const wsUrl = base.replace(/^http/, "ws") + `/ws?token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data) as TenantEvent;
          onEvent?.(event);
        } catch { /* ignore malformed */ }
      };
    })();

    return () => {
      cancelled = true;
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [getToken, onEvent]);
}
