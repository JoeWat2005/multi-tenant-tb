// Full BotConfig editor. All trading fields lock while the session is
// RUNNING (or any active state). Only `model_name` stays editable mid-run
// — Claude picks it up on the next cycle with no restart.
"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Loader2, Lock } from "lucide-react";
import { useApi, useApiMutate } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import type { BotConfig, BotStatus, SessionStatus } from "@/lib/api";

const SYMBOLS = [
  "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LINK/USD",
  "DOGE/USD", "LTC/USD", "BCH/USD", "UNI/USD", "AAVE/USD",
];
const INTERVALS = ["30m", "1h", "4h"];
const MODES: BotConfig["mode"][] = ["PAPER", "LIVE"];

const LOCKED_STATES: SessionStatus[] = ["STARTING", "RUNNING", "STOPPING", "EXITING"];

const SELECT_CLASSES =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function SettingsForm() {
  const { data, mutate } = useApi<BotConfig>("/api/bot/config", { refreshInterval: 5000 });
  const { data: status } = useApi<BotStatus>("/api/bot/status", { refreshInterval: 3000 });
  const { call } = useApiMutate();
  const [form, setForm] = useState<Partial<BotConfig>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => { if (data) setForm(data); }, [data]);

  const locked = (status && LOCKED_STATES.includes(status.session_status));
  const isTradingField = (k: keyof BotConfig) => k !== "model_name";
  const fieldLocked = (k: keyof BotConfig) => Boolean(locked && isTradingField(k));

  const set = <K extends keyof BotConfig>(k: K, v: BotConfig[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await call<BotConfig>("/api/bot/config", "PUT", form);
      await mutate();
    } finally {
      setSaving(false);
    }
  };

  const revert = () => { if (data) setForm(data); };

  if (!data) return (
    <Card><CardContent className="p-6 text-muted-foreground">Loading…</CardContent></Card>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between flex-wrap gap-2">
          <span>Bot Configuration</span>
          {locked && (
            <span className="flex items-center gap-2 text-amber-400 text-sm font-normal">
              <Lock className="size-4" /> Locked — session {status?.session_status}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <Section title="Market">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Field label="Symbol">
              <select
                className={SELECT_CLASSES}
                value={form.symbol ?? ""}
                disabled={fieldLocked("symbol")}
                onChange={(e) => set("symbol", e.target.value)}
              >
                {!SYMBOLS.includes(form.symbol ?? "") && form.symbol && (
                  <option value={form.symbol}>{form.symbol}</option>
                )}
                {SYMBOLS.map((s) => (<option key={s} value={s}>{s}</option>))}
              </select>
            </Field>
            <Field label="Interval">
              <select
                className={SELECT_CLASSES}
                value={form.interval ?? ""}
                disabled={fieldLocked("interval")}
                onChange={(e) => set("interval", e.target.value)}
              >
                {INTERVALS.map((s) => (<option key={s} value={s}>{s}</option>))}
              </select>
            </Field>
            <Field label="Mode" hint={form.mode === "LIVE" ? "Live trading with real funds." : undefined}>
              <select
                className={cn(SELECT_CLASSES,
                  form.mode === "LIVE" && "border-rose-500/50 text-rose-300")}
                value={form.mode ?? "PAPER"}
                disabled={fieldLocked("mode")}
                onChange={(e) => set("mode", e.target.value as BotConfig["mode"])}
              >
                {MODES.map((m) => (<option key={m} value={m}>{m}</option>))}
              </select>
            </Field>
          </div>
        </Section>

        <Separator />

        <Section title="Risk parameters">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Field label="Position size (%)" hint="% of current balance per trade">
              <Input type="number" step="0.5" min={0} max={100}
                value={form.position_size_pct ?? ""}
                disabled={fieldLocked("position_size_pct")}
                onChange={(e) => set("position_size_pct", Number(e.target.value))} />
            </Field>
            <Field label="Min confidence" hint="0-1, e.g. 0.65">
              <Input type="number" step="0.01" min={0} max={1}
                value={form.min_confidence ?? ""}
                disabled={fieldLocked("min_confidence")}
                onChange={(e) => set("min_confidence", Number(e.target.value))} />
            </Field>
            <Field label="Stop loss (%)">
              <Input type="number" step="0.1" min={0} max={100}
                value={form.stop_loss_pct ?? ""}
                disabled={fieldLocked("stop_loss_pct")}
                onChange={(e) => set("stop_loss_pct", Number(e.target.value))} />
            </Field>
            <Field label="Take profit (%)">
              <Input type="number" step="0.1" min={0} max={100}
                value={form.take_profit_pct ?? ""}
                disabled={fieldLocked("take_profit_pct")}
                onChange={(e) => set("take_profit_pct", Number(e.target.value))} />
            </Field>
            <Field label="Max session loss (%)" hint="Kill new buys past this drawdown">
              <Input type="number" step="0.1" min={0} max={100}
                value={form.max_session_loss_pct ?? ""}
                disabled={fieldLocked("max_session_loss_pct")}
                onChange={(e) => set("max_session_loss_pct", Number(e.target.value))} />
            </Field>
            <Field label="Paper balance (USD)">
              <Input type="number" step="1" min={0}
                value={form.paper_balance_usd ?? ""}
                disabled={fieldLocked("paper_balance_usd")}
                onChange={(e) => set("paper_balance_usd", Number(e.target.value))} />
            </Field>
          </div>
        </Section>

        <Separator />

        <Section title="Decision engine">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Sentiment interval (min)">
              <Input type="number" step="1" min={1} max={1440}
                value={form.sentiment_interval_minutes ?? ""}
                disabled={fieldLocked("sentiment_interval_minutes")}
                onChange={(e) => set("sentiment_interval_minutes", Number(e.target.value))} />
            </Field>
            <Field label="Lookback candles">
              <Input type="number" step="1" min={10} max={500}
                value={form.decision_lookback_candles ?? ""}
                disabled={fieldLocked("decision_lookback_candles")}
                onChange={(e) => set("decision_lookback_candles", Number(e.target.value))} />
            </Field>
            <Field label="Model" hint="Claude model id. Editable while running.">
              <Input value={form.model_name ?? ""}
                onChange={(e) => set("model_name", e.target.value)} />
            </Field>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
            <Toggle
              label="Use AI"
              checked={!!form.use_ai}
              onChange={(v) => set("use_ai", v)}
              disabled={fieldLocked("use_ai")}
            />
            <Toggle
              label="Use sentiment"
              checked={!!form.use_sentiment}
              onChange={(v) => set("use_sentiment", v)}
              disabled={fieldLocked("use_sentiment")}
            />
          </div>
        </Section>

        <div className="flex items-center gap-2 pt-2">
          {locked && (
            <Badge variant="outline" className="text-xs text-muted-foreground">
              Stop the session to change trading parameters.
            </Badge>
          )}
          <div className="ml-auto flex gap-2">
            <Button variant="outline" onClick={revert} disabled={saving}>Revert</Button>
            <Button onClick={save} disabled={saving}>
              {saving ? <Loader2 className="animate-spin" /> : null}
              Save changes
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

function Toggle({
  label, checked, onChange, disabled,
}: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className={cn(
      "flex items-center justify-between rounded-md border p-3 cursor-pointer",
      disabled && "opacity-50 cursor-not-allowed",
    )}>
      <span className="text-sm">{label}</span>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </label>
  );
}
