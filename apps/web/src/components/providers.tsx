"use client";

import { useEffect, type CSSProperties, type ReactNode } from "react";
import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, useTheme } from "next-themes";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api";

let browserQueryClient: QueryClient | undefined;

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      },
    },
  });
}

function getQueryClient() {
  if (typeof window === "undefined") return makeClient();
  browserQueryClient ??= makeClient();
  return browserQueryClient;
}

// A tab left open with nobody at it would poll every 30-60s all night and keep the database (Neon, billed by
// awake time) from ever suspending. After IDLE_MS without input we tell React Query the window is unfocused, which
// stops refetchInterval polling; the next input restores normal focus tracking and refetches stale queries.
const IDLE_MS = 10 * 60_000;
const ACTIVITY_EVENTS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"] as const;

function useIdlePause() {
  useEffect(() => {
    let timer: number | undefined;
    let idle = false;
    let lastReset = 0;
    const goIdle = () => {
      idle = true;
      focusManager.setFocused(false);
    };
    const onActivity = () => {
      if (idle) {
        idle = false;
        focusManager.setFocused(undefined); // back to document visibility; refetches what went stale
      }
      const now = Date.now();
      if (now - lastReset < 5_000) return; // pointermove fires constantly; resetting every 5s is plenty
      lastReset = now;
      window.clearTimeout(timer);
      timer = window.setTimeout(goIdle, IDLE_MS);
    };
    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, { passive: true }));
    onActivity();
    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity));
      window.clearTimeout(timer);
      if (idle) focusManager.setFocused(undefined);
    };
  }, []);
}

function IdlePause() {
  useIdlePause();
  return null;
}

// Toasts follow the active theme and use the brand surface tokens.
function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Toaster
      theme={resolvedTheme === "dark" ? "dark" : "light"}
      richColors
      closeButton
      position="bottom-right"
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as CSSProperties
      }
    />
  );
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={getQueryClient()}>
        <IdlePause />
        <TooltipProvider>
          {children}
          <ThemedToaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
