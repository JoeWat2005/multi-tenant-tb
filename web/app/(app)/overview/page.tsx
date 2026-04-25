// Session-only dashboard. Everything on this page is scoped to the active
// (or most recent) trading session.
import { BotControls } from "@/components/overview/bot-controls";
import { StatCards } from "@/components/overview/stat-cards";
import { EquityChart } from "@/components/overview/equity-chart";
import { OpenPositionCard } from "@/components/overview/open-position-card";
import { SessionPerformance } from "@/components/overview/session-performance";
import { DecisionsFeed } from "@/components/overview/decisions-feed";

export default function OverviewPage() {
  return (
    <div className="space-y-6 max-w-7xl">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">Session</h1>
          <p className="text-sm text-muted-foreground">Live session. One ticker, one trade at a time.</p>
        </div>
        <BotControls />
      </div>

      <StatCards />

      <EquityChart />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <OpenPositionCard />
        <SessionPerformance />
      </div>

      <DecisionsFeed />
    </div>
  );
}
