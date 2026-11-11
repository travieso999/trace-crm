"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { NativeSelect } from "@/components/common";
import { api } from "@/lib/api";
import type { PersonRef, TeamMember } from "@/lib/types";

/** Everyone in the workspace (active first). Used by owner pickers and filters. */
export function useTeam() {
  return useQuery({ queryKey: ["team"], queryFn: () => api<TeamMember[]>("/api/auth/team"), staleTime: 60_000 });
}

export const memberName = (m: Pick<TeamMember, "full_name" | "username" | "email">) => m.full_name || m.username || m.email;

function initials(name: string) {
  const parts = name.trim().split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

// Stable, theme-safe tint per person so the same teammate always reads the same on board and table.
const TINTS = [
  "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  "bg-teal-500/15 text-teal-700 dark:text-teal-300",
];

function tint(id: string) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TINTS[h % TINTS.length];
}

/** Initials bubble plus name; "Unassigned" when nobody owns it. */
export function OwnerChip({ owner, compact, className }: { owner: PersonRef | null; compact?: boolean; className?: string }) {
  if (!owner)
    return (
      <span className={cn("inline-flex min-w-0 items-center gap-1.5 text-muted-foreground", className)}>
        <span className="flex size-5 shrink-0 items-center justify-center rounded-full border border-dashed text-[9px]" aria-hidden>?</span>
        {!compact && <span className="truncate">Unassigned</span>}
        {compact && <span className="sr-only">Unassigned</span>}
      </span>
    );
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5", className)} title={owner.is_active ? owner.name : `${owner.name} (inactive)`}>
      <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-full text-[9px] font-semibold", tint(owner.id))} aria-hidden>
        {initials(owner.name)}
      </span>
      {compact ? <span className="sr-only">Assigned to {owner.name}</span> : (
        <span className={cn("truncate", !owner.is_active && "text-muted-foreground line-through decoration-muted-foreground/50")}>{owner.name}</span>
      )}
    </span>
  );
}

/**
 * Assigned To dropdown. ``allowed`` limits the choices for people who can only take or hand back work:
 * "any" = every active teammate; "self" = themselves and Unassigned (plus the current owner, shown read-only).
 */
export function OwnerSelect({
  value,
  onChange,
  meId,
  allowed = "any",
  current,
  includeUnassigned = true,
  className,
  ...props
}: Omit<React.ComponentProps<"select">, "value" | "onChange"> & {
  value: string | null;
  onChange: (ownerId: string | null) => void;
  meId?: string;
  allowed?: "any" | "self";
  current?: PersonRef | null;
  includeUnassigned?: boolean;
}) {
  const team = useTeam();
  const active = (team.data ?? []).filter((m) => m.is_active);
  const options = allowed === "any" ? active : active.filter((m) => m.id === meId);
  const showCurrent = current && !options.some((m) => m.id === current.id);
  return (
    <NativeSelect value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} className={className} {...props}>
      {includeUnassigned && <option value="">Unassigned</option>}
      {showCurrent && <option value={current.id} disabled={allowed !== "any" || !current.is_active}>{current.name}{current.is_active ? "" : " (inactive)"}</option>}
      {options.map((m) => (
        <option key={m.id} value={m.id}>
          {memberName(m)}{m.id === meId ? " (me)" : ""}
        </option>
      ))}
    </NativeSelect>
  );
}
