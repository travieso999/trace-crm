import type { NextConfig } from "next";

// The browser always talks to this origin; /api/* and /u/* (unsubscribe pages) are proxied to FastAPI.
// In production Caddy routes those paths straight to the API container, so these rewrites only matter
// for `next dev` / `next start` without the reverse proxy.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const DEV = process.env.NODE_ENV !== "production";

// Scripts, styles, fonts and API calls only from this origin, so injected markup can't load a script from elsewhere
// or send data to another site. Next's hydration needs inline scripts; 'unsafe-eval' and websockets only in dev.
// img-src allows https: for remote images in emails, which stay blocked until the viewer chooses "Show images".
const CSP = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${DEV ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  `connect-src 'self'${DEV ? " ws: wss:" : ""}`,
  "frame-src 'self'",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  // a second build (e.g. for testing) can go elsewhere without replacing the one being served
  distDir: process.env.NEXT_DIST_DIR || ".next",
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
          { key: "Content-Security-Policy", value: CSP },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
