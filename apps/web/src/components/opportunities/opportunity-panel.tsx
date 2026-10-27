"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Dialog as SheetPrimitive } from "@base-ui/react/dialog";
import { Building2, ExternalLink, Mail, Phone, SquareMinus, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog, ErrorState } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { fullName } from "@/lib/format";
import type { OppDetail, Stage } from "@/lib/types";
import { ActivityTab } from "./activity-tab";
import { NotesTab } from "./notes-tab";
import { OverviewTab } from "./overview-tab";
import { DirtyContext, money } from "./shared";
import { TasksTab } from "./tasks-tab";

export const PANEL_TABS = ["overview", "tasks", "notes", "activity"] as const;
export type PanelTab = (typeof PANEL_TABS)[number];

const STATUS_STYLE: Record<string, string> = {
  open: "bg-muted text-muted-foreground",
  won: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  lost: "bg-zinc-500/15 text-zinc-500",
};

export function OpportunityPanel({ contactId, tab, stages, canWrite, onTab, onClose }: {
  contactId: string | null;
  tab: PanelTab;
  stages: Stage[];
  canWrite: boolean;
  onTab: (t: PanelTab) => void;
  onClose: () => void;
}) {
  const dirty = React.useRef(new Set<string>());
  const [confirmClose, setConfirmClose] = React.useState(false);
  const [removing, setRemoving] = React.useState(false);
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api(`/api/opportunities/${contactId}/remove`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Removed from Opportunities", { description: "The prospect, tasks, notes and history are kept." });
      setRemoving(false);
      dirty.current.clear();
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: ["opportunity-stages"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dirtyCtx = React.useMemo(() => ({
    set: (key: string, d: boolean) => {
      if (d) dirty.current.add(key);
      else dirty.current.delete(key);
    },
  }), []);
  const requestClose = () => {
    if (dirty.current.size) setConfirmClose(true);
    else onClose();
  };
  const detail = useQuery({
    queryKey: ["opportunity", contactId],
    queryFn: () => api<OppDetail>(`/api/opportunities/${contactId}`),
    enabled: !!contactId,
  });
  const d = detail.data;
  return (
    <SheetPrimitive.Root open={!!contactId} onOpenChange={(o) => { if (!o) requestClose(); }}>
      <SheetPrimitive.Portal>
        <SheetPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <SheetPrimitive.Popup
          aria-label={d ? `Opportunity: ${fullName(d)}` : "Opportunity"}
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-full flex-col bg-popover text-popover-foreground shadow-2xl outline-none sm:border-l",
            "sm:w-[72vw] sm:max-w-[1200px] sm:min-w-[640px]",
            "transition-[transform,opacity] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)]",
            "data-ending-style:translate-x-8 data-ending-style:opacity-0 data-starting-style:translate-x-8 data-starting-style:opacity-0",
          )}
        >
          <DirtyContext.Provider value={dirtyCtx}>
            <header className="flex items-start gap-4 border-b px-5 py-4 sm:px-7 sm:py-5">
              <div className="min-w-0 flex-1">
                {d ? (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <SheetPrimitive.Title className="truncate text-xl font-semibold tracking-tight">{fullName(d)}</SheetPrimitive.Title>
                      <span className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs">
                        <span className="size-2 rounded-full" style={{ background: d.stage_color }} aria-hidden /> {d.stage_name}
                      </span>
                      <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium capitalize", STATUS_STYLE[d.status])}>{d.status}</span>
                    </div>
                    <SheetPrimitive.Description className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
                      {d.company_name && <span className="inline-flex items-center gap-1.5"><Building2 className="size-3.5" aria-hidden />{d.company_name}</span>}
                      <a href={`mailto:${d.email}`} className="inline-flex items-center gap-1.5 hover:text-foreground"><Mail className="size-3.5" aria-hidden />{d.email}</a>
                      {d.phone && <a href={`tel:${d.phone}`} className="inline-flex items-center gap-1.5 hover:text-foreground"><Phone className="size-3.5" aria-hidden />{d.phone}</a>}
                      {!!d.value_cents && <span className="tabular-nums">{money(d.value_cents)}</span>}
                    </SheetPrimitive.Description>
                  </>
                ) : (
                  <div className="grid gap-2"><Skeleton className="h-7 w-64" /><Skeleton className="h-4 w-80" /></div>
                )}
              </div>
              {d && (
                <Link href={`/prospects/${d.id}`} className="hidden items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground sm:inline-flex">
                  Prospect record <ExternalLink className="size-3" aria-hidden />
                </Link>
              )}
              {d?.on_board && canWrite && (
                <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setRemoving(true)}>
                  <SquareMinus /> <span className="hidden sm:inline">Remove</span>
                </Button>
              )}
              <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={requestClose}><X /></Button>
            </header>

            {detail.isError ? (
              <div className="p-6"><ErrorState error={detail.error} onRetry={() => detail.refetch()} /></div>
            ) : (
              <Tabs value={tab} onValueChange={(v) => onTab(v as PanelTab)} className="flex min-h-0 flex-1 flex-col">
                <div className="border-b px-5 sm:px-7">
                  <TabsList variant="line" className="h-11 gap-4">
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="tasks">Tasks{d && d.open_tasks > 0 && <span className="ml-1 rounded-full bg-primary/15 px-1.5 text-[11px] text-signal tabular-nums">{d.open_tasks}</span>}</TabsTrigger>
                    <TabsTrigger value="notes">Notes</TabsTrigger>
                    <TabsTrigger value="activity">Activity</TabsTrigger>
                  </TabsList>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 sm:px-7">
                  {!d ? (
                    <div className="grid gap-3"><Skeleton className="h-10" /><Skeleton className="h-40" /></div>
                  ) : (
                    <>
                      <TabsContent value="overview" className="animate-page-in"><OverviewTab detail={d} stages={stages} canWrite={canWrite} onOpenTab={(t) => onTab(t as PanelTab)} /></TabsContent>
                      <TabsContent value="tasks" className="animate-page-in"><TasksTab contactId={d.id} canWrite={canWrite} /></TabsContent>
                      <TabsContent value="notes" className="animate-page-in"><NotesTab contactId={d.id} canWrite={canWrite} /></TabsContent>
                      <TabsContent value="activity" className="animate-page-in"><ActivityTab contactId={d.id} /></TabsContent>
                    </>
                  )}
                </div>
              </Tabs>
            )}
          </DirtyContext.Provider>
        </SheetPrimitive.Popup>
      </SheetPrimitive.Portal>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title="Remove from Opportunities?"
        description="It leaves the board. Nothing is deleted: the prospect stays in Prospects and campaigns, and its tasks, notes and history are kept. You can add it back any time."
        confirmLabel="Remove"
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
      <ConfirmDialog
        open={confirmClose}
        onOpenChange={setConfirmClose}
        title="Discard unsaved changes?"
        description="You have edits that haven’t been saved. Closing will lose them."
        confirmLabel="Discard and close"
        destructive
        onConfirm={() => {
          setConfirmClose(false);
          dirty.current.clear();
          onClose();
        }}
      />
    </SheetPrimitive.Root>
  );
}
