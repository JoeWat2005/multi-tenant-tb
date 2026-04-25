// Root layout — loads globals.css (Tailwind + shadcn tokens) and wraps the
// tree in ClerkProvider. Defaults to dark mode for the trading aesthetic.
import type { ReactNode } from "react";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

export const metadata = {
  title: "Claude Trader",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en" className="dark">
        <body className="min-h-screen bg-background text-foreground antialiased">{children}</body>
      </html>
    </ClerkProvider>
  );
}
