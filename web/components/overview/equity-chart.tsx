// Equity curve for the current session, rendered with lightweight-charts.
// The amber dashed line is the starting balance — drawn via the series'
// own price-line API so it works whether we have one sample or a hundred
// (a two-point line series throws when both timestamps match).
"use client";

import { useEffect, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createChart, ColorType, LineStyle } from "lightweight-charts";
import type { IChartApi, IPriceLine, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { useApi } from "@/hooks/use-api";
import type { EquityPoint, TradingSession } from "@/lib/api";
import { parseBackendDate } from "@/lib/format";

const GREEN = "#22c55e";
const AMBER = "#eab308";

export function EquityChart() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const areaRef = useRef<ISeriesApi<"Area"> | null>(null);
  const baselineRef = useRef<IPriceLine | null>(null);

  const { data: points } = useApi<EquityPoint[]>(
    "/api/session/equity?limit=500",
    { refreshInterval: 5000 },
  );
  const { data: sessionData } = useApi<{ session: TradingSession | null }>(
    "/api/session/current",
    { refreshInterval: 5000 },
  );

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(240,240,240,0.7)",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      handleScroll: false,
      handleScale: false,
    });
    chartRef.current = chart;

    areaRef.current = chart.addAreaSeries({
      lineColor: GREEN,
      lineWidth: 2,
      topColor: "rgba(34,197,94,0.25)",
      bottomColor: "rgba(34,197,94,0.01)",
      priceLineVisible: false,
    });

    return () => {
      chart.remove();
      chartRef.current = null;
      areaRef.current = null;
      baselineRef.current = null;
    };
  }, []);

  useEffect(() => {
    const area = areaRef.current;
    if (!area || !points) return;

    // Lightweight-charts rejects duplicate or out-of-order times.
    const seen = new Set<number>();
    const data = points
      .map((p) => {
        const d = parseBackendDate(p.created_at);
        if (!d) return null;
        const t = Math.floor(d.getTime() / 1000);
        if (seen.has(t)) return null;
        seen.add(t);
        return { time: t as UTCTimestamp, value: p.total_equity };
      })
      .filter((p): p is { time: UTCTimestamp; value: number } => p !== null)
      .sort((a, b) => a.time - b.time);

    area.setData(data);

    // Horizontal reference line at the starting balance. createPriceLine
    // draws across the full chart width, so there's no time-ordering trap.
    const startBal = sessionData?.session?.starting_balance ?? data[0]?.value;
    if (baselineRef.current) {
      area.removePriceLine(baselineRef.current);
      baselineRef.current = null;
    }
    if (startBal != null && startBal > 0) {
      baselineRef.current = area.createPriceLine({
        price: startBal,
        color: AMBER,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "start",
      });
    }

    chartRef.current?.timeScale().fitContent();
  }, [points, sessionData?.session?.starting_balance]);

  return (
    <Card>
      <CardHeader className="pb-3 flex flex-row items-center justify-between">
        <CardTitle>Equity</CardTitle>
        <div className="text-xs text-muted-foreground">
          {points?.length ?? 0} sample{(points?.length ?? 0) === 1 ? "" : "s"}
        </div>
      </CardHeader>
      <CardContent>
        <div ref={containerRef} className="h-64 w-full" />
        {(points ?? []).length === 0 && (
          <div className="text-xs text-muted-foreground text-center -mt-36 pointer-events-none">
            Equity data appears once the session has started.
          </div>
        )}
      </CardContent>
    </Card>
  );
}
