import type { NextConfig } from "next";

// The browser always talks to this origin; /api/* and /u/* (unsubscribe pages) are proxied to FastAPI.
// In production Caddy routes those paths straight to the API container, so these rewrites only matter
// for `next dev` / `next start` without the reverse proxy.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Renamed pages keep their old links and bookmarks working
  async redirects() {
    return [
      { source: "/pipeline", destination: "/opportunities", permanent: true },
      // Leads were renamed Prospects
      { source: "/leads", destination: "/prospects", permanent: true },
      { source: "/leads/:path*", destination: "/prospects/:path*", permanent: true },
    ];
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
      { source: "/u/:path*", destination: `${API_URL}/u/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          // no framing, no plugins, no <base> hijack, forms only post back to this app
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
