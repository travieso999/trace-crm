"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { cn } from "cn";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type KeyboardCoordinateGetter,
} from "@dnd-kit/core";
import { AlarmClock, CalendarClock, Columns3, KanbanSquare, Mail, Phone, Plus, Rows3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, PageHeader } from "@/components/common";
import { useCan, useCanWrite, useIsAdmin, useMe } from "@/components/app-shell";
import { OwnerChip } from "@/components/team";
import { FilterBar, apiFilters, useOppFilters } from "@/components/opportunities/filters";
import { OpportunityTable } from "@/components/opportunities/opportunity-table";
import { OpportunityPanel, PANEL_TABS, type PanelTab } from "@/components/opportunities/opportunity-panel";
import { StageManager } from "@/components/opportunities/stage-manager";
import { NewOpportunityDialog } from "@/components/opportunities/new-opportunity";
import { dueLabel, money } from "@/components/opportunities/shared";
import { api, errorMessage } from "@/lib/api";
import { fullName, num } from "@/lib/format";
import type { BoardStage, OppCard, Stage } from "@/lib/types";

// Keyboard dragging: each ←/→ press moves the card one whole column (dnd-kit's default step is 25px).
const columnJump: KeyboardCoordinateGetter = (event, { currentCoordinates, context: { droppableRects, collisionRect } }) => {
  const dir = event.code === "ArrowRight" ? 1 : event.code === "ArrowLeft" ? -1 : 0;
  if (!dir || !collisionRect) return undefined;
  event.preventDefault();
  const cx = collisionRect.left + collisionRect.width / 2;
  const columns = [...droppableRects.values()].filter((r) => !!r).sort((a, b) => a.left - b.left);
  const next = dir > 0 ? columns.find((r) => r.left > cx) : [...columns].reverse().find((r) => r.right < cx);
  if (!next) return undefined;
  return { x: next.left + 8 + (currentCoordinates.x - collisionRect.left), y: next.top + 44 + (currentCoordinates.y - collisionRect.top) };
};

function Card({ card, canWrite, active, onOpen }: { card: OppCard; canWrite: boolean; active: boolean; onOpen: () => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: card.id, data: { stage: card.stage_id }, disabled: !canWrite });
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined;
  const t = card.next_task;
  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      role="button"
      aria-roledescription="Draggable opportunity"
      aria-label={`${fullName(card)}${card.company_name ? `, ${card.company_name}` : ""}, ${card.stage_name}. Enter to open${canWrite ? ", space to move" : ""}.`}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen();
        listeners?.onKeyDown?.(e);
      }}
      onClick={onOpen}
      className={cn(
        "group grid cursor-pointer gap-1.5 rounded-lg border bg-card p-3 text-sm shadow-xs outline-none transition-[border-color,box-shadow] hover:border-foreground/25 focus-visible:ring-2 focus-visible:ring-ring",
        canWrite && "touch-none",
        active && "border-primary/60",
        isDragging && "z-10 opacity-90 shadow-xl ring-2 ring-primary",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium">{fullName(card)}</div>
          <div className="truncate text-xs text-muted-foreground">{card.company_name || "No business name"}</div>
        </div>
        {!!card.value_cents && <span className="shrink-0 text-xs font-medium tabular-nums">{money(card.value_cents)}</span>}
      </div>
      <div className="grid gap-0.5 text-xs text-muted-foreground">
        <span className="flex min-w-0 items-center gap-1.5"><Mail className="size-3 shrink-0" aria-hidden /><span className="truncate">{card.email}</span></span>
        {card.phone && <span className="flex items-center gap-1.5"><Phone className="size-3 shrink-0" aria-hidden />{card.phone}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
        <span className="inline-flex items-center gap-1 rounded-full border px-1.5 py-px text-[11px]">
          <span className="size-1.5 rounded-full" style={{ background: card.stage_color }} aria-hidden />{card.stage_name}
        </span>
        {card.status !== "open" && <span className="rounded-full bg-muted px-1.5 py-px text-[11px] capitalize">{card.status}</span>}
        <OwnerChip owner={card.owner} className="ml-auto max-w-32 text-[11px]" />
        {t && (
          <span className={cn("inline-flex min-w-0 items-center gap-1 text-[11px]", t.overdue ? "font-medium text-destructive" : "text-muted-foreground")}
            title={`${t.task_type}: ${t.title}`}>
            {t.overdue ? <AlarmClock className="size-3 shrink-0" aria-hidden /> : <CalendarClock className="size-3 shrink-0" aria-hidden />}
            <span className="truncate">{t.overdue ? "Overdue · " : ""}{dueLabel(t)}</span>
          </span>
        )}
      </div>
    </div>
  );
}

function Column({ stage, canWrite, openId, onOpen }: { stage: BoardStage; canWrite: boolean; openId: string | null; onOpen: (id: string) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id: stage.id });
  return (
    <section
      ref={setNodeRef}
      aria-label={`${stage.name}: ${stage.count} opportunities`}
      className={cn("flex min-w-72 flex-1 basis-72 flex-col rounded-xl border bg-muted/30 transition-colors", isOver && "border-primary bg-primary/5")}
    >
      <header className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold">
          <span className="size-2.5 shrink-0 rounded-full" style={{ background: stage.color }} aria-hidden />
          <span className="truncate">{stage.name}</span>
        </h2>
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {num(stage.count)}{stage.value_cents > 0 && ` · ${money(stage.value_cents)}`}
        </span>
      </header>
      <div className="grid max-h-[calc(100vh-15rem)] min-h-28 content-start gap-2 overflow-y-auto overscroll-contain p-2">
        {stage.items.map((c) => <Card key={c.id} card={c} canWrite={canWrite} active={c.id === openId} onOpen={() => onOpen(c.id)} />)}
        {stage.items.length === 0 && <p className="px-1 py-6 text-center text-xs text-muted-foreground">Drop opportunities here</p>}
        {stage.count > stage.items.length && (
          <p className="px-1 text-xs text-muted-foreground">+{num(stage.count - stage.items.length)} more. Use search to find them.</p>
        )}
      </div>
    </section>
  );
}

function OpportunitiesBoard() {
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const isAdmin = useIsAdmin();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const meId = useMe().data?.user.id;
  const canAssignAny = useCan("opportunities.reassign");
  const canTake = useCan("opportunities.assign_self");
  const canCreate = useCan("opportunities.create");
  const { filters, set, clear, active, view, setView, replace } = useOppFilters();
  const openId = params.get("open");
  const tabParam = params.get("tab");
  const tab: PanelTab = PANEL_TABS.includes(tabParam as PanelTab) ? (tabParam as PanelTab) : "overview";
  const setPanel = React.useCallback((id: string | null, t?: PanelTab) => {
    const next = new URLSearchParams(params.toString());
    if (id) {
      next.set("open", id);
      next.set("tab", t ?? "overview");
    } else {
      next.delete("open");
      next.delete("tab");
    }
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [params, pathname, router]);

  // search box: typed locally, written to the URL (and so to the API) after a short pause
  const [search, setSearch] = React.useState(filters.q);
  const [seenQ, setSeenQ] = React.useState(filters.q);
  if (filters.q !== seenQ) {
    setSeenQ(filters.q);
    setSearch(filters.q);
  }
  React.useEffect(() => {
    const t = setTimeout(() => { if (search.trim() !== filters.q) set({ q: search.trim() }); }, 250);
    return () => clearTimeout(t);
  }, [search, filters.q, set]);
  const query = apiFilters(filters, filters.q);
  const boardKey = ["opportunities", "board", query] as const;
  const page = Math.max(1, Number(params.get("page")) || 1);
  const sort = { key: params.get("sort") || "updated", order: (params.get("order") === "asc" ? "asc" : "desc") as "asc" | "desc" };
  const [managing, setManaging] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const stages = useQuery({ queryKey: ["opportunity-stages"], queryFn: () => api<Stage[]>("/api/opportunities/stages") });
  const board = useQuery({
    queryKey: boardKey,
    queryFn: () => api<{ stages: BoardStage[] }>("/api/opportunities", { query }),
    enabled: view === "board",
  });
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: columnJump }));

  const move = useMutation({
    mutationFn: ({ id, to }: { id: string; to: string }) => api(`/api/opportunities/${id}/move`, { body: { stage_id: to } }),
    onMutate: async ({ id, to }) => {
      await qc.cancelQueries({ queryKey: boardKey });
      const prev = qc.getQueryData<{ stages: BoardStage[] }>(boardKey);
      if (prev) {
        const card = prev.stages.flatMap((s) => s.items).find((c) => c.id === id);
        const target = prev.stages.find((s) => s.id === to);
        if (card && target)
          qc.setQueryData(boardKey, {
            stages: prev.stages.map((s) => {
              const without = s.items.filter((c) => c.id !== id);
              if (s.id === to) return { ...s, count: s.count + 1, items: [{ ...card, stage_id: s.id, stage_name: s.name, stage_color: s.color }, ...without] };
              return without.length !== s.items.length ? { ...s, count: s.count - 1, items: without } : s;
            }),
          });
      }
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(boardKey, ctx.prev);
      toast.error(errorMessage(e));
    },
    onSuccess: (_r, { id, to }) => {
      const s = stages.data?.find((x) => x.id === to);
      toast.success(`Moved to ${s?.name ?? "stage"}`, { description: s?.lead_status === "not_interested" ? "Active campaign sends for this prospect were stopped." : undefined });
      qc.invalidateQueries({ queryKey: ["opportunity", id] });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: ["opportunity-stages"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
    },
  });

  function onDragEnd(e: DragEndEvent) {
    const from = e.active.data.current?.stage as string | undefined;
    const to = e.over?.id as string | undefined;
    if (to && from && to !== from) move.mutate({ id: String(e.active.id), to });
  }

  const cols = board.data?.stages ?? [];
  const total = cols.reduce((s, x) => s + x.count, 0);
  const totalValue = cols.reduce((s, x) => s + x.value_cents, 0);
  return (
    <>
      <PageHeader
        title="Opportunities"
        description={view === "board" && board.data ? `${num(total)} ${active ? "matching" : total === 1 ? "opportunity" : "opportunities"}${totalValue ? ` · ${money(totalValue)} in deals` : ""}` : "Every opportunity in the workspace, with who owns it."}
        actions={
          <div className="flex flex-wrap gap-2">
            <div role="tablist" aria-label="View" className="inline-flex rounded-lg border p-0.5">
              {([["board", KanbanSquare, "Board"], ["table", Rows3, "Table"]] as const).map(([v, Icon, label]) => (
                <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)}
                  className={cn("inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground",
                    view === v && "bg-muted font-medium text-foreground")}>
                  <Icon className="size-4" aria-hidden />{label}
                </button>
              ))}
            </div>
            {isAdmin && <Button variant="outline" onClick={() => setManaging(true)} disabled={!stages.data}><Columns3 /> Manage stages</Button>}
            {canWrite && canCreate && <Button onClick={() => setCreating(true)} disabled={!stages.data}><Plus /> New opportunity</Button>}
          </div>
        }
      />
      <FilterBar filters={filters} set={set} clear={clear} active={active} search={search} onSearch={setSearch}
        stages={stages.data ?? []} meId={meId} />
      {view === "table" ? (
        <OpportunityTable
          query={query}
          page={page}
          onPage={(p) => replace((x) => (p > 1 ? x.set("page", String(p)) : x.delete("page")))}
          sort={sort}
          onSort={(s) => replace((x) => { x.set("sort", s.key); x.set("order", s.order); x.delete("page"); })}
          meId={meId}
          canAssignAny={canAssignAny}
          canTake={canTake}
          openId={openId}
          onOpen={(id) => setPanel(id)}
          filtered={active > 0}
        />
      ) : board.isError ? (
        <ErrorState error={board.error} onRetry={() => board.refetch()} />
      ) : board.isPending ? (
        <div className="flex gap-3 overflow-hidden">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-96 w-72 shrink-0" />)}</div>
      ) : (
        <>
        {total === 0 && !active && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed px-4 py-3 text-sm animate-page-in">
            <span className="text-muted-foreground">No opportunities yet. Prospects from campaigns stay in Prospects until you add them here.</span>
            {canWrite && canCreate && <Button size="sm" onClick={() => setCreating(true)} disabled={!stages.data}><Plus /> New opportunity</Button>}
          </div>
        )}
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          <div className={cn("-mx-4 overflow-x-auto px-4 pb-3 md:-mx-6 md:px-6 lg:-mx-8 lg:px-8", board.isPlaceholderData && "opacity-70")}>
            <div className="flex gap-3">
              {cols.map((s) => <Column key={s.id} stage={s} canWrite={canWrite} openId={openId} onOpen={(id) => setPanel(id)} />)}
            </div>
          </div>
        </DndContext>
        </>
      )}
      <OpportunityPanel
        contactId={openId}
        tab={tab}
        stages={stages.data ?? []}
        canWrite={canWrite}
        onTab={(t) => setPanel(openId, t)}
        onClose={() => setPanel(null)}
      />
      {isAdmin && stages.data && <StageManager open={managing} onOpenChange={setManaging} stages={stages.data} />}
      {canWrite && canCreate && stages.data && (
        <NewOpportunityDialog open={creating} onOpenChange={setCreating} stages={stages.data} onCreated={(id) => setPanel(id)} />
      )}
    </>
  );
}

export default function OpportunitiesPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-96" />}>
      <OpportunitiesBoard />
    </React.Suspense>
  );
}
