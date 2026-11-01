"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, ArchiveRestore, MoreHorizontal, Pause, Play, Plus, RotateCcw, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, NativeSelect, PageHeader, StatusBadge } from "@/components/common";
import { NewCampaignDialog } from "@/components/campaign/new-campaign-dialog";
import { SendConfirmDialog } from "@/components/campaign/send-confirm-dialog";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { num, pct, relTime } from "@/lib/format";
import type { Campaign } from "@/lib/types";

const STATUS_FILTERS = [
  ["", "All"],
  ["active", "Active"],
  ["draft", "Draft"],
  ["scheduled", "Scheduled"],
  ["paused,error", "Paused"],
  ["completed,stopped", "Finished"],
] as const;

const ACTION_DONE: Record<string, string> = { pause: "Campaign paused", resume: "Campaign resumed", archive: "Campaign archived", unarchive: "Campaign restored" };

function RowActions({ c, onAction, onRelaunch, onDelete }: {
  c: Campaign; onAction: (c: Campaign, name: string) => void; onRelaunch: (c: Campaign) => void; onDelete: (c: Campaign) => void;
}) {
  const running = c.status === "active" || c.status === "scheduled";
  const finished = c.status === "completed" || c.status === "stopped";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${c.name}`} />}>
        <MoreHorizontal />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {finished && (
          <DropdownMenuItem onClick={() => onRelaunch(c)}>
            <RotateCcw /> Relaunch
          </DropdownMenuItem>
        )}
        {(c.status === "paused" || c.status === "error") && !c.archived_at && (
          <DropdownMenuItem onClick={() => onAction(c, "resume")}>
            <Play /> Resume
          </DropdownMenuItem>
        )}
        {running && (
          <DropdownMenuItem onClick={() => onAction(c, "pause")}>
            <Pause /> Pause
          </DropdownMenuItem>
        )}
        {c.archived_at ? (
          <DropdownMenuItem onClick={() => onAction(c, "unarchive")}>
            <ArchiveRestore /> Restore from archive
          </DropdownMenuItem>
        ) : (
          !running && (
            <DropdownMenuItem onClick={() => onAction(c, "archive")}>
              <Archive /> Archive
            </DropdownMenuItem>
          )
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => onDelete(c)}>
          <Trash2 /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CampaignsList() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [deleting, setDeleting] = React.useState<Campaign | null>(null);
  const [relaunched, setRelaunched] = React.useState<Campaign | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["campaigns"] });
  const action = useMutation({
    mutationFn: ({ c, name }: { c: Campaign; name: string }) => api<Campaign>(`/api/campaigns/${c.id}/${name}`, { method: "POST" }),
    onSuccess: (_, { name }) => {
      toast.success(ACTION_DONE[name] ?? "Done");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: (c: Campaign) => api(`/api/campaigns/${c.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Campaign deleted");
      setDeleting(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const relaunch = useMutation({
    mutationFn: (c: Campaign) => api<Campaign>(`/api/campaigns/${c.id}/relaunch`, { method: "POST" }),
    onSuccess: (copy) => {
      setRelaunched(copy);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const start = useMutation({
    mutationFn: (c: Campaign) => api<Campaign>(`/api/campaigns/${c.id}/start`, { method: "POST" }),
    onSuccess: (c) => {
      toast.success(c.status === "scheduled" ? "Campaign scheduled" : "Campaign relaunched");
      setRelaunched(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const canWrite = useCanWrite();
  const [status, setStatus] = React.useState("");
  const [archived, setArchived] = React.useState(false);
  // ?new=1 (e.g. from Imported Lists) opens the new-campaign wizard
  const [creating, setCreating] = React.useState(params.get("new") === "1");

  const list = useQuery({
    queryKey: ["campaigns", status, archived],
    queryFn: () => api<Campaign[]>("/api/campaigns", { query: { status, archived } }),
  });

  const items = list.data ?? [];
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="Named groups of prospects, each with an initial email and follow-ups sent from your mailboxes."
        actions={
          canWrite && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> New campaign
            </Button>
          )
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <NativeSelect aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUS_FILTERS.map(([v, l]) => (
            <option key={l} value={v}>
              {l}
            </option>
          ))}
        </NativeSelect>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
          Show archived
        </label>
      </div>

      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <div className="grid gap-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={Send}
          title={status || archived ? "No campaigns match" : "No campaigns yet"}
          description={status || archived ? "Try another filter." : "Create a campaign, write its sequence, enroll prospects and start sending."}
          action={
            canWrite && !status && !archived && (
              <Button onClick={() => setCreating(true)}>
                <Plus /> New campaign
              </Button>
            )
          }
        />
      ) : (
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campaign</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Prospects</TableHead>
                <TableHead className="hidden text-right sm:table-cell">Sent</TableHead>
                <TableHead className="hidden text-right md:table-cell">Replies</TableHead>
                <TableHead className="hidden text-right md:table-cell">Reply rate</TableHead>
                <TableHead className="hidden lg:table-cell">Emails</TableHead>
                <TableHead className="hidden lg:table-cell">Created</TableHead>
                {canWrite && <TableHead><span className="sr-only">Actions</span></TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="max-w-72">
                    <Link href={`/campaigns/${c.id}`} className="block truncate font-medium hover:underline">
                      {c.name}
                    </Link>
                    {c.last_error ? (
                      <div className="truncate text-xs text-destructive">{c.last_error}</div>
                    ) : (c.target_location || c.industry) && (
                      <div className="truncate text-xs text-muted-foreground">{[c.industry, c.target_location].filter(Boolean).join(" · ")}</div>
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={c.status} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{num(c.stats.leads)}</TableCell>
                  <TableCell className="hidden text-right tabular-nums sm:table-cell">{num(c.stats.sent)}</TableCell>
                  <TableCell className="hidden text-right tabular-nums md:table-cell">{num(c.stats.replied)}</TableCell>
                  <TableCell className="hidden text-right tabular-nums md:table-cell">{pct(c.stats.reply_rate)}</TableCell>
                  <TableCell className="hidden lg:table-cell">{c.steps.length}</TableCell>
                  <TableCell className="hidden text-muted-foreground lg:table-cell">{relTime(c.created_at)}</TableCell>
                  {canWrite && (
                    <TableCell className="w-10 text-right">
                      <RowActions c={c} onAction={(x, name) => action.mutate({ c: x, name })} onRelaunch={(x) => relaunch.mutate(x)} onDelete={setDeleting} />
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <NewCampaignDialog open={creating} onOpenChange={setCreating} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name}”?`}
        description="Sending stops immediately. Sent history and replies stay in the inbox and analytics."
        confirmLabel="Delete campaign"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting)}
      />
      {relaunched && (
        <SendConfirmDialog
          campaign={relaunched}
          open
          onOpenChange={(o) => {
            if (!o) {
              toast.info(`“${relaunched.name}” is saved as a draft. Nothing was sent.`);
              setRelaunched(null);
            }
          }}
          pending={start.isPending}
          onConfirm={() => start.mutate(relaunched)}
          onFix={(tab) => router.push(`/campaigns/${relaunched.id}?tab=${tab}`)}
        />
      )}
    </>
  );
}

export default function CampaignsPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-64" />}>
      <CampaignsList />
    </React.Suspense>
  );
}
