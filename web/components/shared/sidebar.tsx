// Left-hand nav. Kept intentionally dumb — links + active-route highlight.
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, ListOrdered, Settings, User, LayoutDashboard } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/overview", label: "Session", icon: LayoutDashboard },
  { href: "/trades", label: "Fills", icon: ListOrdered },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/account", label: "Account", icon: User },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-1 p-3">
      <div className="flex items-center gap-2 px-3 py-2 mb-2">
        <BarChart3 className="size-5" />
        <span className="font-semibold text-sm">Claude Trader</span>
      </div>
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname?.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
              active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/50",
            )}
          >
            <Icon className="size-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
