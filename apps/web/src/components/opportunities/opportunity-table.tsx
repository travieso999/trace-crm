"use client";

import * as React from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, KanbanSquare, UserRoundCheck } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorState, Pagination, Spinner } from "@/components/common";
import { OwnerChip, OwnerSelect } from "@/components/team";
import { api, errorMessage } from "@/lib/api";
import { dateTime, fullName, num, relTime, shortDate } from "@/lib/format";
import type { OppList, OppRow } from "@/lib/types";
import { money } from "./shared";

const PAGE_SIZE = 50;
type Sort = { key: string; order: "asc" | "desc" };

function SortHead({ label, k, sort, onSort, className }: { label: string; k: string; sort: Sort; onSort: (s: Sort) => void; className?: string }) {
  const active = sort.key === k;
  return (
    <TableHead className={className} aria-sort={active ? (sort.order === "asc" ? "ascending" : "descending") : undefined}>
      <button type="button" className="inline-flex items-center gap-1 hover:text-foreground"
        onClick={() => onSort({ key: k, order: active && sort.order === "desc" ? "asc" : active ? "desc" : k === "name" || k === "owner" ? "asc" : "desc" })}>
        {label}
        {active && (sort.order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </TableHead>
  );
}

/** Inline Assigned To for one row; only offers what the user may actually do. */
function RowOwner({ row, meId, canAssignAny, canTake }: { row: OppRow; meId?: string; canAssignAny: boolean; canTake: boolean }) {
  const qc = useQueryClient();
  const assign = useMutation({
    mutationFn: (owner_id: string | null) => api(`/api/opportunities/${row.id}`, { method: "PATCH", body: { owner_id } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: ["opportunity", row.id] });
      qc.invalidateQueries({ queryKey: ["opportunity-activity", row.id] });
    },
    onError: (e) => toast.error("Couldn’t reassign", { description: errorMessage(e) }),
  });
  const mine = row.owner?.id === meId;
  const selfOnly = !canAssignAny && canTake && (!row.owner || mine);
  if (!canAssignAny && !selfOnly) return <OwnerChip owner={row.owner} className="max-w-44 text-sm" />;
  return (
    <span className="inline-flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      <OwnerSelect
        aria-label={`Assigned to for ${fullName(row)}`}
        value={row.owner?.id ?? null}
        current={row.owner}
        meId={meId}
        allowed={canAssignAny ? "any" : "self"}
        disabled={assign.isPending}
        className="h-7 max-w-44 border-transparent bg-transparent px-1.5 hover:border-input"
        onChange={(id) => assign.mutate(id)}
      />
      {assign.isPending && <Spinner />}
    </span>
  );
}

function BulkAssignBar({ ids, meId, onDone }: { ids: string[]; meId?: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [owner, setOwner] = React.useState<string | null>(meId ?? null);
  const bulk = useMutation({
    mutationFn: () => api<{ updated: number; matched: number }>("/api/opportunities/bulk-assign", { body: { ids, owner_id: owner } }),
    onSuccess: (r) => {
      toast.success(`${num(r.updated)} opportunit${r.updated === 1 ? "y" : "ies"} reassigned`,
        { description: r.matched > r.updated ? `${num(r.matched - r.updated)} already had that owner.` : undefined });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      onDone();
    },
    onError: (e) => toast.error("Couldn’t reassign", { description: errorMessage(e) }),
  });
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2 text-sm animate-page-in" role="region" aria-label="Bulk actions">
      <span className="font-medium">{num(ids.length)} selected</span>
      <span className="text-muted-foreground">Assign to</span>
      <OwnerSelect aria-label="Assign selected to" value={owner} meId={meId} onChange={setOwner} />
      <Button size="sm" disabled={bulk.isPending} onClick={() => bulk.mutate()}>
        {bulk.isPending ? <Spinner className="text-primary-foreground" /> : <UserRoundCheck />} Assign
      </Button>
      <Button size="sm" variant="ghost" onClick={onDone}>Clear selection</Button>
    </div>
  );
}

export function OpportunityTable({ query, page, onPage, sort, onSort, meId, canAssignAny, canTake, openId, onOpen, filtered }: {
  query: Record<string, string | undefined>;
  page: number;
  onPage: (p: number) => void;
  sort: Sort;
  onSort: (s: Sort) => void;
  meId?: string;
  canAssignAny: boolean;
  canTake: boolean;
  openId: string | null;
  onOpen: (id: string) => void;
  filtered: boolean;
}) {
  const list = useQuery({
    queryKey: ["opportunities", "list", query, sort, page],
    queryFn: ({ signal }) => api<OppList>("/api/opportunities/list", {
      query: { ...query, sort: sort.key, order: sort.order, page, page_size: PAGE_SIZE }, signal,
    }),
    placeholderData: keepPreviousData,
  });
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const resetKey = JSON.stringify(query);
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setSelected(new Set());
  }
  if (list.isError) return <ErrorState error={list.error} onRetry={() => list.refetch()} />;
  if (list.isPending) return <div className="grid gap-2">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-11" />)}</div>;
  const items = list.data.items;
  if (items.length === 0)
    return (
      <EmptyState icon={KanbanSquare} title={filtered ? "No opportunities match these filters" : "No opportunities yet"}
        description={filtered ? "Try a different search, or clear the filters." : "Add one with New opportunity."} />
    );
  const allOnPage = items.every((r) => selected.has(r.id));
  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });
  return (
    <>
      {canAssignAny && selected.size > 0 && <BulkAssignBar ids={[...selected]} meId={meId} onDone={() => setSelected(new Set())} />}
      <div className="rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              {canAssignAny && (
                <TableHead className="w-10">
                  <Checkbox aria-label="Select all on this page" checked={allOnPage}
                    onCheckedChange={(v) => setSelected((s) => {
                      const n = new Set(s);
                      items.forEach((r) => (v ? n.add(r.id) : n.delete(r.id)));
                      return n;
                    })} />
                </TableHead>
              )}
              <SortHead label="Opportunity" k="name" sort={sort} onSort={onSort} />
              <SortHead label="Assigned To" k="owner" sort={sort} onSort={onSort} />
              <SortHead label="Stage" k="stage" sort={sort} onSort={onSort} className="hidden md:table-cell" />
              <TableHead className="hidden sm:table-cell">Status</TableHead>
              <SortHead label="Value" k="value" sort={sort} onSort={onSort} className="hidden lg:table-cell" />
              <SortHead label="Last activity" k="activity" sort={sort} onSort={onSort} className="hidden lg:table-cell" />
              <SortHead label="Created" k="created" sort={sort} onSort={onSort} className="hidden xl:table-cell" />
            </TableRow>
          </TableHeader>
          <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
            {items.map((r) => (
              <TableRow key={r.id} data-state={selected.has(r.id) || r.id === openId ? "selected" : undefined}
                className="cursor-pointer" onClick={() => onOpen(r.id)}>
                {canAssignAny && (
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Checkbox aria-label={`Select ${fullName(r)}`} checked={selected.has(r.id)} onCheckedChange={() => toggle(r.id)} />
                  </TableCell>
                )}
                <TableCell className="max-w-72">
                  <button type="button" className="block max-w-full truncate text-left font-medium outline-none hover:underline focus-visible:underline"
                    onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}>
                    {fullName(r)}
                  </button>
                  <div className="truncate text-xs text-muted-foreground">{r.company_name || r.email}</div>
                </TableCell>
                <TableCell><RowOwner row={r} meId={meId} canAssignAny={canAssignAny} canTake={canTake} /></TableCell>
                <TableCell className="hidden md:table-cell">
                  <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm">
                    <span className="size-2 rounded-full" style={{ background: r.stage_color }} aria-hidden />{r.stage_name}
                  </span>
                </TableCell>
                <TableCell className="hidden sm:table-cell">
                  <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium capitalize",
                    r.status === "won" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground")}>{r.status}</span>
                </TableCell>
                <TableCell className="hidden tabular-nums lg:table-cell">{r.value_cents ? money(r.value_cents) : "—"}</TableCell>
                <TableCell className="hidden text-muted-foreground lg:table-cell" title={dateTime(r.last_activity_at)}>{relTime(r.last_activity_at)}</TableCell>
                <TableCell className="hidden text-muted-foreground xl:table-cell" title={dateTime(r.created_at)}>{shortDate(r.created_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={onPage} />
    </>
  );
}
