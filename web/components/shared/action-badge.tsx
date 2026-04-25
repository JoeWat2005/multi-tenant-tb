// Action badge reused across decisions feed, trades table, symbol page.
// BUY green / SELL red / HOLD amber — matches v1 dashboard colours.
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Action = "BUY" | "SELL" | "HOLD" | string;

export function ActionBadge({ action, className }: { action: Action; className?: string }) {
  const variant =
    action === "BUY" ? "success" : action === "SELL" ? "danger" : action === "HOLD" ? "warning" : "outline";
  return (
    <Badge variant={variant} className={cn("w-16 justify-center", className)}>
      {action}
    </Badge>
  );
}
