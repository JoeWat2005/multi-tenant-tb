// Shell for authenticated screens — sidebar + user menu.
import type { ReactNode } from "react";
import { UserButton } from "@clerk/nextjs";
import { Sidebar } from "@/components/shared/sidebar";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="w-60 border-r bg-card/50">
        <Sidebar />
      </aside>
      <div className="flex-1 flex flex-col">
        <header className="h-14 border-b flex items-center justify-end px-6">
          <UserButton />
        </header>
        <main className="flex-1 p-6 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
