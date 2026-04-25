// Shared display formatters. Keep UI-adjacent so components stay dumb.

// SQLite strips tzinfo on read, so backend ISO strings sometimes come back
// without a `Z` / `+00:00` suffix. Treat tz-naive strings as UTC (which is
// always the backend's intent) so `new Date()` doesn't interpret them as
// local time and off-by-an-hour everything.
export function parseBackendDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasTz ? iso : iso + "Z");
}

export function fmtUSD(n: number | null | undefined, opts: { digits?: number } = {}) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: opts.digits ?? 2,
    maximumFractionDigits: opts.digits ?? 2,
  });
}

export function fmtPct(n: number | null | undefined, opts: { digits?: number; signed?: boolean } = {}) {
  if (n == null || Number.isNaN(n)) return "—";
  const digits = opts.digits ?? 2;
  const sign = opts.signed && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

export function fmtRelativeTime(iso: string | null | undefined): string {
  const d = parseBackendDate(iso);
  if (!d) return "—";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function fmtDuration(startIso: string, endIso: string | null): string {
  const start = parseBackendDate(startIso);
  if (!start) return "—";
  const end = parseBackendDate(endIso)?.getTime() ?? Date.now();
  const diff = (end - start.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
  return `${Math.floor(diff / 86400)}d ${Math.floor((diff % 86400) / 3600)}h`;
}
