"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Plus, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, NativeSelect, PageHeader, StatusBadge } from "@/components/common";
import { NewCampaignDialog } from "@/components/campaign/new-campaign-dialog";
import { useCanWrite } from "@/components/app-shell";
import { api } from "@/lib/api";
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

function CampaignsList() {
  const params = useSearchParams();
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
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <NewCampaignDialog open={creating} onOpenChange={setCreating} />
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
