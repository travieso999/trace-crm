"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { cn } from "cn";
import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Building2,
  ChevronsLeft,
  ChevronsRight,
  FileSearch,
  Inbox,
  KanbanSquare,
  LayoutDashboard,
  ListOrdered,
  LogOut,
  Mail,
  Menu,
  Monitor,
  Moon,
  Send,
  Settings,
  ShieldBan,
  Sparkles,
  Sun,
  Users,
  FileSpreadsheet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { NotificationBell } from "@/components/notification-bell";
import { TraceMark } from "@/components/brand";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

type NavItem = { href: string; label: string; icon: LucideIcon };

export const NAV_GROUPS: { label: string | null; items: NavItem[] }[] = [
  { label: null, items: [{ href: "/", label: "Overview", icon: LayoutDashboard }] },
  {
    label: "Sales",
    items: [
      { href: "/opportunities", label: "Opportunities", icon: KanbanSquare },
      { href: "/prospects", label: "Prospects", icon: Users },
      { href: "/lists", label: "Imported Lists", icon: FileSpreadsheet },
      { href: "/companies", label: "Companies", icon: Building2 },
    ],
  },
  {
    label: "Outreach",
    items: [
      { href: "/campaigns", label: "Campaigns", icon: Send },
      { href: "/sequences", label: "Sequences", icon: ListOrdered },
      { href: "/inbox", label: "Unified Inbox", icon: Inbox },
      { href: "/email-accounts", label: "Email Accounts", icon: Mail },
    ],
  },
  {
    label: "Insights & tools",
    items: [
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/ai", label: "AI Personalization", icon: Sparkles },
      { href: "/audits", label: "Website Audits", icon: FileSearch },
    ],
  },
  {
    label: "Admin",
    items: [
      { href: "/suppressions", label: "Suppression List", icon: ShieldBan },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export const NAV = NAV_GROUPS.flatMap((g) => g.items);

type Me = { user: User; csrf_token: string };

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/auth/me"), staleTime: 60_000, retry: false });
}

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");
}

function NavList({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const unread = useQuery({
    queryKey: ["inbox-unread"],
    queryFn: () => api<{ unread: number }>("/api/inbox/unread-count"),
    refetchInterval: 60_000,
  }).data?.unread;
  return (
    <nav aria-label="Main" className="flex flex-col px-2.5">
      {NAV_GROUPS.map((group, gi) => (
        <div key={group.label ?? "top"} role="group" aria-label={group.label ?? undefined} className={cn("flex flex-col gap-1", gi > 0 && "mt-5")}>
          {group.label && (collapsed ? (
            <div className="mx-2 mb-1 h-px bg-sidebar-border" aria-hidden />
          ) : (
            <div className="mb-1 px-3 text-[11px] font-semibold tracking-[0.12em] text-sidebar-foreground/45 uppercase animate-label-in">{group.label}</div>
          ))}
      {group.items.map(({ href, label, icon: Icon }) => {
        const active = isActive(pathname, href);
        const link = (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative flex h-10 items-center gap-3 rounded-lg px-3 text-[15px] text-sidebar-foreground/75 outline-none transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring",
              active &&
                "bg-sidebar-accent font-medium text-sidebar-foreground before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-sidebar-primary",
              collapsed && "justify-center px-0",
            )}
          >
            <Icon className={cn("size-[18px] shrink-0", active && "text-signal")} aria-hidden />
            {!collapsed && <span className="animate-label-in truncate">{label}</span>}
            {collapsed && <span className="sr-only">{label}</span>}
            {href === "/inbox" && !!unread && (
              <span
                className={cn(
                  "rounded-full bg-primary px-1.5 text-[10px] font-semibold leading-4 text-primary-foreground tabular-nums",
                  collapsed ? "absolute right-1 top-0.5" : "ml-auto",
                )}
              >
                {unread > 99 ? "99+" : unread}
                <span className="sr-only"> unread</span>
              </span>
            )}
          </Link>
        );
        return collapsed ? (
          <Tooltip key={href}>
            <TooltipTrigger render={<div />}>{link}</TooltipTrigger>
            <TooltipContent side="right">{label}</TooltipContent>
          </Tooltip>
        ) : (
          link
        );
      })}
        </div>
      ))}
    </nav>
  );
}

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <Link
      href="/"
      className={cn(
        "flex h-16 shrink-0 items-center gap-3 px-5 outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        collapsed && "justify-center px-0",
      )}
    >
      <TraceMark className="h-6 text-sidebar-foreground" title="Trace Studio" />
      {!collapsed && (
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-tight">Trace CRM</div>
          <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">Trace Studio</div>
        </div>
      )}
    </Link>
  );
}

function ThemeMenuItems() {
  // next-themes persists the choice in localStorage ("theme").
  const { theme, setTheme } = useTheme();
  return (
    <DropdownMenuGroup>
      <DropdownMenuLabel>Theme</DropdownMenuLabel>
      <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={(v) => setTheme(String(v))}>
        <DropdownMenuRadioItem value="light">
          <Sun /> Light
        </DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="dark">
          <Moon /> Dark
        </DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="system">
          <Monitor /> System
        </DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
    </DropdownMenuGroup>
  );
}

// Sidebar collapsed flag persisted in localStorage; in-memory fallback when storage is unavailable.
const COLLAPSED_KEY = "ts-sidebar-collapsed";
const collapsedListeners = new Set<() => void>();
let collapsedMemory = false;

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return collapsedMemory;
  }
}

function writeCollapsed(value: boolean) {
  collapsedMemory = value;
  try {
    localStorage.setItem(COLLAPSED_KEY, value ? "1" : "0");
  } catch {
    /* storage unavailable */
  }
  collapsedListeners.forEach((l) => l());
}

function subscribeCollapsed(listener: () => void) {
  collapsedListeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    collapsedListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const collapsed = React.useSyncExternalStore(subscribeCollapsed, readCollapsed, () => false);
  const fullWidth = usePathname().startsWith("/opportunities");
  const [mobileOpen, setMobileOpen] = React.useState(false);

  const toggle = React.useCallback(() => writeCollapsed(!readCollapsed()), []);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  React.useEffect(() => {
    if (me.isError) router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
  }, [me.isError, router]);

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      qc.clear();
      router.replace("/login");
    }
  }

  if (me.isPending || me.isError) {
    return (
      <div className="flex min-h-screen">
        <div className="hidden w-64 border-r bg-sidebar p-4 md:block">
          <Skeleton className="mb-6 h-7 w-32" />
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="mb-2 h-6 w-full" />
          ))}
        </div>
        <div className="flex-1 p-8">
          <Skeleton className="mb-4 h-7 w-48" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  const user = me.data.user;
  const initials = (user.full_name || user.email).slice(0, 2).toUpperCase();

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r bg-sidebar md:flex",
          collapsed ? "w-16" : "w-64",
        )}
      >
        <Brand collapsed={collapsed} />
        <div className="flex-1 overflow-y-auto py-3">
          <NavList collapsed={collapsed} />
        </div>
        <div className="border-t p-2">
          <Button
            variant="ghost"
            size="sm"
            className={cn("w-full justify-start text-muted-foreground", collapsed && "justify-center")}
            onClick={toggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <ChevronsRight /> : <ChevronsLeft />}
            {!collapsed && <span>Collapse</span>}
            {!collapsed && <kbd className="ml-auto text-[10px] text-muted-foreground">Ctrl B</kbd>}
          </Button>
        </div>
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-64 bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Brand collapsed={false} />
          <NavList collapsed={false} onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background px-4 md:px-6">
          <Button variant="ghost" size="icon-sm" className="md:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu />
          </Button>
          <Link href="/" className="flex items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden">
            <TraceMark className="h-4" title="Trace Studio" />
            <span className="text-sm font-semibold tracking-tight">Trace CRM</span>
          </Link>
          <div className="flex-1" />
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" className="gap-2 px-1.5" aria-label="Account menu">
                  <span className="flex size-7 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-signal">
                    {initials}
                  </span>
                  <span className="hidden text-sm sm:inline">{user.full_name || user.email}</span>
                </Button>
              }
            />
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuGroup>
                <DropdownMenuLabel>
                  <div className="text-sm font-medium text-foreground">{user.email}</div>
                  <div className="text-xs capitalize">{user.role}</div>
                </DropdownMenuLabel>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <ThemeMenuItems />
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => router.push("/settings")}>
                <Settings /> Settings
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={logout}>
                <LogOut /> Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <NotificationBell />
        </header>
        <main id="main" className={cn("mx-auto w-full flex-1 px-4 py-6 md:px-6 lg:px-8", !fullWidth && "max-w-[1400px]")}>
          {children}
        </main>
      </div>
    </div>
  );
}

export function useCanWrite() {
  const me = useMe();
  return me.data ? me.data.user.role !== "viewer" : false;
}

/** Mailboxes, settings and suppression removal are admin-only on the API. */
export function useIsAdmin() {
  const me = useMe();
  return me.data?.user.role === "admin";
}
