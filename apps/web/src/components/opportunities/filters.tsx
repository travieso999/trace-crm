"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CalendarRange, Search, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/common";
import { memberName, useTeam } from "@/components/team";
import type { Stage } from "@/lib/types";

/** Filter keys as they appear in the URL. */
export const FILTER_KEYS = ["q", "owner", "status", "stage", "cfrom", "cto", "afrom", "ato"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];
export type Filters = Record<FilterKey, string>;

const STORE = "ts-opp-filters";
const EMPTY: Filters = { q: "", owner: "", status: "", stage: "", cfrom: "", cto: "", afrom: "", ato: "" };

function readStore(): Partial<Filters> & { view?: string } {
  try {
    return JSON.parse(localStorage.getItem(STORE) ?? "{}");
  } catch {
    return {};
  }
}

function writeStore(v: Partial<Filters> & { view?: string }) {
  try {
    localStorage.setItem(STORE, JSON.stringify(v));
  } catch {
    /* storage unavailable: filters still live in the URL */
  }
}

/**
 * Opportunity filters kept in the URL, so opening and closing an opportunity (or Back) keeps them, and remembered in
 * this browser so returning to the page restores the last view.
 */
export function useOppFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters: Filters = React.useMemo(
    () => Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Filters,
    [params],
  );
  const view = params.get("view") === "table" ? "table" : "board";

  const replace = React.useCallback((mutate: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params.toString());
    mutate(next);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [params, pathname, router]);

  // restore the last filters when arriving without any in the URL
  const restored = React.useRef(false);
  React.useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    if ([...FILTER_KEYS, "view"].some((k) => params.has(k))) return;
    const saved = readStore();
    const entries = Object.entries(saved).filter(([k, v]) => v && ([...FILTER_KEYS, "view"] as string[]).includes(k));
    if (entries.length) replace((p) => entries.forEach(([k, v]) => p.set(k, String(v))));
  }, [params, replace]);

  React.useEffect(() => {
    if (restored.current) writeStore({ ...filters, view });
  }, [filters, view]);

  const set = React.useCallback((patch: Partial<Filters>) => replace((p) => {
    for (const [k, v] of Object.entries(patch)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    p.delete("page");
  }), [replace]);

  const setView = React.useCallback((v: "board" | "table") => replace((p) => {
    if (v === "table") p.set("view", "table");
    else p.delete("view");
  }), [replace]);

  const clear = React.useCallback(() => set({ ...EMPTY }), [set]);
  const active = FILTER_KEYS.filter((k) => k !== "q" && filters[k]).length + (filters.q ? 1 : 0);
  return { filters, set, clear, active, view, setView, replace, params };
}

/** The filters as API query parameters (search is passed separately, debounced). */
export function apiFilters(f: Filters, q: string) {
  return {
    q: q || undefined, owner: f.owner || undefined, status: f.status || undefined, stage_id: f.stage || undefined,
    created_from: f.cfrom || undefined, created_to: f.cto || undefined,
    activity_from: f.afrom || undefined, activity_to: f.ato || undefined,
  };
}

function Segment({ value, current, onClick, children }: { value: string; current: string; onClick: (v: string) => void; children: React.ReactNode }) {
  const on = current === value;
  return (
    <button type="button" role="tab" aria-selected={on} onClick={() => onClick(value)}
      className={cn("rounded-md px-2.5 py-1 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
        on && "bg-background font-medium text-foreground shadow-xs dark:bg-muted")}>
      {children}
    </button>
  );
}

export function FilterBar({ filters, set, clear, active, search, onSearch, stages, meId }: {
  filters: Filters;
  set: (p: Partial<Filters>) => void;
  clear: () => void;
  active: number;
  search: string;
  onSearch: (v: string) => void;
  stages: Stage[];
  meId?: string;
}) {
  const team = useTeam();
  const members = team.data ?? [];
  const specific = filters.owner && !["me", "unassigned"].includes(filters.owner) ? filters.owner : "";
  const datesSet = !!(filters.cfrom || filters.cto || filters.afrom || filters.ato);
  const [showDates, setShowDates] = React.useState(datesSet);
  const ownerTab = filters.owner === "me" ? "me" : filters.owner === "unassigned" ? "unassigned" : specific ? "person" : "";
  return (
    <div className="mb-4 grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Whose opportunities" className="inline-flex rounded-lg border bg-muted/40 p-0.5">
          <Segment value="" current={ownerTab} onClick={() => set({ owner: "" })}>All opportunities</Segment>
          <Segment value="me" current={ownerTab} onClick={() => set({ owner: "me" })}>My opportunities</Segment>
          <Segment value="unassigned" current={ownerTab} onClick={() => set({ owner: "unassigned" })}>Unassigned</Segment>
        </div>
        <NativeSelect aria-label="Filter by owner" value={specific || (filters.owner === "me" && meId ? meId : "")}
          onChange={(e) => set({ owner: e.target.value === meId ? "me" : e.target.value })}
          className={cn(specific && "border-primary/60")}>
          <option value="">Assigned to: anyone</option>
          {members.map((m) => (
            <option key={m.id} value={m.id}>{memberName(m)}{m.id === meId ? " (me)" : ""}{m.is_active ? "" : " (inactive)"}</option>
          ))}
        </NativeSelect>
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" placeholder="Search name, business, email, phone, owner…" aria-label="Search opportunities"
            className="pl-8" value={search} onChange={(e) => onSearch(e.target.value)} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <NativeSelect aria-label="Status" value={filters.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">Any status</option>
          <option value="open">Open</option>
          <option value="won">Won</option>
          <option value="lost">Lost</option>
        </NativeSelect>
        <NativeSelect aria-label="Pipeline stage" value={filters.stage} onChange={(e) => set({ stage: e.target.value })}>
          <option value="">Any stage</option>
          {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </NativeSelect>
        <Button variant={datesSet ? "secondary" : "outline"} size="sm" aria-expanded={showDates} onClick={() => setShowDates(!showDates)}>
          <CalendarRange /> Dates{datesSet ? " · on" : ""}
        </Button>
        {active > 0 && (
          <Button variant="ghost" size="sm" onClick={() => { onSearch(""); clear(); }}>
            <X /> Clear filters
          </Button>
        )}
      </div>
      {showDates && (
        <div className="flex flex-wrap items-end gap-x-4 gap-y-2 rounded-lg border bg-muted/30 px-3 py-2 text-sm animate-page-in">
          <fieldset className="flex flex-wrap items-center gap-1.5">
            <legend className="sr-only">Date created</legend>
            <span className="text-muted-foreground">Created</span>
            <Input type="date" aria-label="Created from" className="h-8 w-36" value={filters.cfrom} max={filters.cto || undefined} onChange={(e) => set({ cfrom: e.target.value })} />
            <span className="text-muted-foreground">to</span>
            <Input type="date" aria-label="Created to" className="h-8 w-36" value={filters.cto} min={filters.cfrom || undefined} onChange={(e) => set({ cto: e.target.value })} />
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-1.5">
            <legend className="sr-only">Last activity</legend>
            <span className="text-muted-foreground">Last activity</span>
            <Input type="date" aria-label="Last activity from" className="h-8 w-36" value={filters.afrom} max={filters.ato || undefined} onChange={(e) => set({ afrom: e.target.value })} />
            <span className="text-muted-foreground">to</span>
            <Input type="date" aria-label="Last activity to" className="h-8 w-36" value={filters.ato} min={filters.afrom || undefined} onChange={(e) => set({ ato: e.target.value })} />
          </fieldset>
        </div>
      )}
    </div>
  );
}
