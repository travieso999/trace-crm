"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import type { OppTask, TaskBrief } from "@/lib/types";

export const REMINDER_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "No reminder" },
  { value: "0", label: "At due time" },
  { value: "15", label: "15 minutes before" },
  { value: "60", label: "1 hour before" },
  { value: "1440", label: "1 day before" },
];

export function reminderLabel(minutes: number | null) {
  if (minutes === null) return null;
  return REMINDER_OPTIONS.find((o) => o.value === String(minutes))?.label ?? `${minutes} min before`;
}

export const money = (cents: number | null | undefined) =>
  ((cents ?? 0) / 100).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/** Short zone label for an IANA name in the viewer's locale, e.g. "CDT". */
export function zoneAbbr(iso: string, tz: string | null) {
  if (!tz) return "";
  try {
    return new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" })
      .formatToParts(new Date(iso)).find((p) => p.type === "timeZoneName")?.value ?? tz;
  } catch {
    return tz;
  }
}

/** "Mar 10, 2030 · 2:30 PM CDT" in the task's own timezone. */
export function dueLabel(t: Pick<OppTask, "due_at" | "due_date" | "due_time" | "timezone"> | TaskBrief) {
  if (!t.due_at) return "No due date";
  const date = "due_date" in t && t.due_date ? t.due_date : null;
  const time = "due_time" in t && t.due_time ? t.due_time : null;
  if (date && time) {
    const [h, m] = time.split(":").map(Number);
    const d = new Date(`${date}T00:00:00`);
    d.setHours(h, m);
    return `${format(d, "MMM d, yyyy · h:mm a")} ${zoneAbbr(t.due_at, t.timezone)}`.trim();
  }
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: t.timezone ?? undefined, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(t.due_at));
  } catch {
    return format(new Date(t.due_at), "MMM d, yyyy h:mm a");
  }
}

export function useInvalidateOpportunity(contactId: string) {
  const qc = useQueryClient();
  return React.useCallback(
    (...extra: string[]) => {
      qc.invalidateQueries({ queryKey: ["opportunity", contactId] });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      for (const k of extra) qc.invalidateQueries({ queryKey: [k, contactId] });
    },
    [qc, contactId],
  );
}

// Unsaved-changes registry: each editor reports whether it holds unsaved input, so closing can ask first.
type DirtyCtx = { set: (key: string, dirty: boolean) => void };
export const DirtyContext = React.createContext<DirtyCtx>({ set: () => {} });

export function useDirty(key: string, dirty: boolean) {
  const { set } = React.useContext(DirtyContext);
  React.useEffect(() => {
    set(key, dirty);
    return () => set(key, false);
  }, [key, dirty, set]);
}
