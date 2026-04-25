import type { NextConfig } from "next";

// Dev-only proxy: browser hits /api/* on :3001, Next rewrites to FastAPI
// on :8001. Keeps the browser origin consistent and avoids CORS preflights.
// Swap for direct calls with CORS in production.
const config: NextConfig = {
  // Whitelist non-localhost origins that may hit the dev server
  // (e.g. Tailscale IP when accessing from another device on your tailnet).
  allowedDevOrigins: ["100.116.106.5"],

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"}/api/:path*`,
      },
    ];
  },
};

export default config;
