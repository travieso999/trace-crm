"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlarmClock, Bell, BellRing, CheckCheck, Circle, CircleCheck, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { relTime } from "@/lib/format";
import type { AppNotification } from "@/lib/types";
import { dueLabel } from "@/components/opportunities/shared";

type Filter = "all" | "unread" | "overdue";
const FILTERS: [Filter, string][] = [["all", "All"], ["unread", "Unread"], ["overdue", "Overdue"]];
const DESKTOP_KEY = "ts-desktop-alerts";
const SEEN_KEY = "ts-notify-seen-at";

function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, v: string) {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* storage unavailable */
  }
}

/** Desktop alerts: only after the user opted in and the browser granted permission. */
function useDesktopAlerts(latestAt: string | null | undefined, open: (path: string) => void) {
  const [enabled, setEnabled] = React.useState(() => typeof window !== "undefined" && read(DESKTOP_KEY) === "1"
    && "Notification" in window && Notification.permission === "granted");
  React.useEffect(() => {
    if (!enabled || !latestAt) return;
    const seen = read(SEEN_KEY);
    if (!seen) {
      write(SEEN_KEY, latestAt); // first run: don't replay old notifications
      return;
    }
    if (latestAt <= seen) return;
    write(SEEN_KEY, latestAt);
    api<{ items: AppNotification[] }>("/api/notifications", { query: { filter: "unread", limit: 5 } }).then((r) => {
      for (const n of r.items.filter((x) => x.created_at > seen).reverse()) {
        const alert = new Notification(n.title, { body: [n.contact_name, n.task_type].filter(Boolean).join(" · "), tag: n.id });
        alert.onclick = () => {
          window.focus();
          if (n.contact_id) open(`/opportunities?open=${n.contact_id}&tab=tasks`);
        };
      }
    }).catch(() => {});
  }, [enabled, latestAt, open]);
  const enable = async () => {
    if (!("Notification" in window)) return toast.error("This browser doesn’t support desktop notifications");
    const p = await Notification.requestPermission();
    if (p === "granted") {
      write(DESKTOP_KEY, "1");
      if (latestAt) write(SEEN_KEY, latestAt);
      setEnabled(true);
      toast.success("Desktop alerts on", { description: "New reminders will pop up while Trace CRM is open in a tab." });
    } else toast.error("Desktop alerts were not allowed", { description: "You can change this in the browser’s site settings." });
  };
  const disable = () => {
    write(DESKTOP_KEY, "0");
    setEnabled(false);
  };
  return { enabled, enable, disable, supported: typeof window !== "undefined" && "Notification" in window };
}

function Item({ n, onOpen }: { n: AppNotification; onOpen: (n: AppNotification) => void }) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["notifications"] });
    qc.invalidateQueries({ queryKey: ["notifications-count"] });
  };
  const mark = useMutation({
    mutationFn: (readFlag: boolean) => api(`/api/notifications/${n.id}/read`, { method: "POST", body: { read: readFlag } }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dismiss = useMutation({
    mutationFn: () => api(`/api/notifications/${n.id}/dismiss`, { method: "POST" }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const overdue = n.task_status === "overdue";
  return (
    <li className={cn("group relative flex gap-3 border-b px-3 py-3 transition-colors last:border-b-0 hover:bg-muted/50", !n.read && "bg-primary/[0.04]")}>
      <span className={cn("mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full", overdue ? "bg-destructive/15 text-destructive" : "bg-primary/15 text-signal")}>
        {overdue ? <AlarmClock className="size-3.5" aria-hidden /> : <BellRing className="size-3.5" aria-hidden />}
      </span>
      <button type="button" onClick={() => onOpen(n)} className="grid min-w-0 flex-1 gap-0.5 text-left outline-none focus-visible:underline">
        <span className={cn("truncate text-sm", !n.read && "font-semibold")}>{n.title}</span>
        <span className="truncate text-xs text-muted-foreground">{[n.contact_name, n.task_type].filter(Boolean).join(" · ")}</span>
        <span className={cn("text-xs", overdue ? "text-destructive" : "text-muted-foreground")}>
          {n.due_at ? `${overdue ? "Overdue · " : "Due "}${dueLabel({ id: "", title: "", task_type: "", overdue, due_at: n.due_at, timezone: n.timezone })}` : ""}
          {n.task_status === "completed" && " · done"}
        </span>
        <time className="text-[11px] text-muted-foreground/80" dateTime={n.created_at}>{relTime(n.created_at)}</time>
      </button>
      <div className="flex shrink-0 flex-col gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
        <Button variant="ghost" size="icon-xs" aria-label={n.read ? "Mark as unread" : "Mark as read"} title={n.read ? "Mark as unread" : "Mark as read"}
          onClick={() => mark.mutate(!n.read)}>
          {n.read ? <Circle /> : <CircleCheck />}
        </Button>
        <Button variant="ghost" size="icon-xs" aria-label="Dismiss" title="Dismiss" onClick={() => dismiss.mutate()}><X /></Button>
      </div>
      {!n.read && <span className="absolute top-4 left-1 size-1.5 rounded-full bg-primary" aria-label="Unread" />}
    </li>
  );
}

export function NotificationBell() {
  const qc = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [filter, setFilter] = React.useState<Filter>("all");
  const count = useQuery({
    queryKey: ["notifications-count"],
    queryFn: () => api<{ unread: number; latest_at: string | null }>("/api/notifications/unread-count"),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const list = useQuery({
    queryKey: ["notifications", filter],
    queryFn: () => api<{ items: AppNotification[]; unread: number }>("/api/notifications", { query: { filter } }),
    enabled: open,
  });
  // a new notification arrived while the panel is open: refresh its list
  const latest = count.data?.latest_at;
  React.useEffect(() => {
    if (open && latest) qc.invalidateQueries({ queryKey: ["notifications"] });
  }, [latest, open, qc]);
  const go = React.useCallback((path: string) => router.push(path, { scroll: false }), [router]);
  const desktop = useDesktopAlerts(latest, go);
  const readAll = useMutation({
    mutationFn: () => api("/api/notifications/read-all", { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["notifications-count"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const openItem = (n: AppNotification) => {
    if (!n.read) api(`/api/notifications/${n.id}/read`, { method: "POST", body: { read: true } })
      .then(() => qc.invalidateQueries({ queryKey: ["notifications-count"] })).catch(() => {});
    setOpen(false);
    if (n.contact_id) router.push(`/opportunities?open=${n.contact_id}&tab=tasks`, { scroll: false });
  };
  const unread = count.data?.unread ?? 0;
  const items = list.data?.items ?? [];
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button variant="ghost" size="icon-sm" className="relative" aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}>
            <Bell />
            {unread > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white tabular-nums animate-page-in" aria-hidden>
                {unread > 99 ? "99+" : unread}
              </span>
            )}
          </Button>
        }
      />
      <PopoverContent align="end" sideOffset={8} className="w-[min(24rem,calc(100vw-1.5rem))] gap-0 overflow-hidden p-0">
        <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
          <h2 className="text-sm font-semibold">Notifications</h2>
          <Button variant="ghost" size="xs" disabled={!unread || readAll.isPending} onClick={() => readAll.mutate()}><CheckCheck /> Mark all read</Button>
        </div>
        <div role="tablist" aria-label="Filter notifications" className="flex gap-1 border-b px-2 py-1.5">
          {FILTERS.map(([v, l]) => (
            <button key={v} type="button" role="tab" aria-selected={filter === v} onClick={() => setFilter(v)}
              className={cn("rounded-md px-2.5 py-1 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                filter === v ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground")}>
              {l}{v === "unread" && unread > 0 ? ` (${unread})` : ""}
            </button>
          ))}
        </div>
        <div className="max-h-[min(28rem,60vh)] overflow-y-auto overscroll-contain">
          {list.isPending ? (
            <div className="flex justify-center p-6"><Spinner /></div>
          ) : items.length === 0 ? (
            <div className="grid justify-items-center gap-1 px-6 py-10 text-center">
              <Bell className="size-5 text-muted-foreground" aria-hidden />
              <p className="text-sm font-medium">{filter === "all" ? "You’re all caught up" : filter === "unread" ? "No unread notifications" : "Nothing overdue"}</p>
              <p className="text-xs text-muted-foreground">Task reminders from Opportunities appear here.</p>
            </div>
          ) : (
            <ul>{items.map((n) => <Item key={n.id} n={n} onOpen={openItem} />)}</ul>
          )}
        </div>
        {desktop.supported && (
          <div className="flex items-center justify-between gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
            <span>{desktop.enabled ? "Desktop alerts are on" : "Get desktop alerts for new reminders"}</span>
            <Button variant="ghost" size="xs" onClick={desktop.enabled ? desktop.disable : desktop.enable}>{desktop.enabled ? "Turn off" : "Turn on"}</Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
