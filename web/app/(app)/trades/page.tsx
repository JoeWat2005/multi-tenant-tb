import { TradesTable } from "@/components/trades/trades-table";

export default function TradesPage() {
  return (
    <div className="space-y-6 max-w-7xl">
      <div>
        <h1 className="text-2xl font-semibold">Fills</h1>
        <p className="text-sm text-muted-foreground">Every order executed in this session.</p>
      </div>
      <TradesTable />
    </div>
  );
}
