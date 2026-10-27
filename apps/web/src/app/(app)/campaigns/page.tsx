"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, Field, NativeSelect, PageHeader, StatusBadge } from "@/components/common";
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

export default function CampaignsPage() {
  const router = useRouter();
  const canWrite = useCanWrite();
  const [status, setStatus] = React.useState("");
  const [archived, setArchived] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [name, setName] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  const list = useQuery({
    queryKey: ["campaigns", status, archived],
    queryFn: () => api<Campaign[]>("/api/campaigns", { query: { status, archived } }),
  });

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const c = await api<Campaign>("/api/campaigns", { body: { name: name.trim() } });
      router.push(`/campaigns/${c.id}?tab=sequence`);
    } catch (err) {
      toast.error(errorMessage(err));
      setSaving(false);
    }
  }

  const items = list.data ?? [];
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="Sequences sent from your mailboxes to enrolled prospects."
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
                <TableHead className="hidden lg:table-cell">Steps</TableHead>
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
                    {c.last_error && <div className="truncate text-xs text-destructive">{c.last_error}</div>}
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

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New campaign</DialogTitle>
          </DialogHeader>
          <form onSubmit={create} className="grid gap-4">
            <Field label="Name" htmlFor="camp-name" hint="Only your team sees this.">
              <Input id="camp-name" autoFocus maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="Texas bail bonds — Q4" />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setCreating(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saving || !name.trim()}>
                Create
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
