"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, FileSpreadsheet, MoreHorizontal, Pencil, Send, Trash2, Upload, Users } from "lucide-react";
import { cn } from "cn";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, NativeSelect, PageHeader, StatusBadge } from "@/components/common";
import { useCanWrite } from "@/components/app-shell";
import { api, download, errorMessage } from "@/lib/api";
import { dateTime, num, relTime } from "@/lib/format";
import type { Campaign, EnrollResult, ImportList } from "@/lib/types";

/** Add a whole imported list to a campaign: one click, no picking prospects one by one. */
export function AddListToCampaignDialog({ list, onOpenChange }: { list: ImportList | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const campaigns = useQuery({
    queryKey: ["campaigns", "open"],
    queryFn: () => api<Campaign[]>("/api/campaigns", { query: { status: "draft,scheduled,active,paused" } }),
    enabled: !!list,
  });
  const [campaignId, setCampaignId] = React.useState("");
  const chosen = campaignId || campaigns.data?.[0]?.id || "";
  const add = useMutation({
    mutationFn: () => api<EnrollResult>(`/api/campaigns/${chosen}/contacts`, { body: { batch_id: list!.id } }),
    onSuccess: (r) => {
      const name = campaigns.data?.find((c) => c.id === chosen)?.name;
      const skipped = r.skipped_existing + r.skipped_suppressed + r.skipped_invalid;
      toast.success(`${num(r.enrolled)} prospect${r.enrolled === 1 ? "" : "s"} added to “${name}”`, {
        description: skipped
          ? `${num(r.skipped_existing)} already in it · ${num(r.skipped_suppressed)} suppressed · ${num(r.skipped_invalid)} invalid or closed`
          : undefined,
      });
      for (const k of ["import-lists", "campaign", "campaigns", "campaign-leads"]) qc.invalidateQueries({ queryKey: [k] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!list} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add “{list?.name}” to a campaign</DialogTitle>
          <DialogDescription>
            All {num(list?.contacts)} prospects in the list are added at once. Suppressed, unsubscribed, invalid and closed prospects are skipped, and
            anyone already in the campaign is left as is.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (chosen) add.mutate();
          }}
        >
          {campaigns.data && campaigns.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No open campaigns.{" "}
              <Link href="/campaigns?new=1" className="text-signal hover:underline">
                Create one
              </Link>{" "}
              and choose this list as its audience.
            </p>
          ) : (
            <Field label="Campaign" htmlFor="alc-campaign">
              <NativeSelect id="alc-campaign" className="w-full" value={chosen} onChange={(e) => setCampaignId(e.target.value)}>
                {(campaigns.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} · {c.status}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!chosen || add.isPending}>
              <Send /> Add {num(list?.contacts)} prospects
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RenameDialog({ list, onOpenChange }: { list: ImportList | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [name, setName] = React.useState("");
  const [forId, setForId] = React.useState<string | null>(null);
  if (list && forId !== list.id) {
    setForId(list.id);
    setName(list.name);
  }
  const save = useMutation({
    mutationFn: () => api<ImportList>(`/api/imports/${list!.id}`, { method: "PATCH", body: { name } }),
    onSuccess: () => {
      toast.success("List renamed");
      qc.invalidateQueries({ queryKey: ["import-lists"] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!list} onOpenChange={(o) => { if (!o) setForId(null); onOpenChange(o); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Rename list</DialogTitle>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) save.mutate();
          }}
        >
          <Field label="Name" htmlFor="rl-name">
            <Input id="rl-name" autoFocus maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || save.isPending}>
              Save
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function ImportedListsPage() {
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const lists = useQuery({ queryKey: ["import-lists"], queryFn: () => api<ImportList[]>("/api/imports") });
  const [adding, setAdding] = React.useState<ImportList | null>(null);
  const [renaming, setRenaming] = React.useState<ImportList | null>(null);
  const [deleting, setDeleting] = React.useState<ImportList | null>(null);
  const del = useMutation({
    mutationFn: (id: string) => api<{ message: string }>(`/api/imports/${id}`, { method: "DELETE" }),
    onSuccess: (r) => {
      toast.success(r.message);
      qc.invalidateQueries({ queryKey: ["import-lists"] });
      setDeleting(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  async function exportList(l: ImportList) {
    try {
      await download(`/api/imports/${l.id}/export`, undefined, `${l.name}.csv`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  const items = lists.data ?? [];
  return (
    <>
      <PageHeader
        title="Imported Lists"
        description="Every uploaded spreadsheet, kept as a named group you can add to campaigns in one click. Prospects are stored once in Prospects."
        actions={
          canWrite && (
            <Link href="/prospects/import" className={cn(buttonVariants())}>
              <Upload /> Upload spreadsheet
            </Link>
          )
        }
      />
      {lists.isError ? (
        <ErrorState error={lists.error} onRetry={() => lists.refetch()} />
      ) : lists.isPending ? (
        <div className="grid gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={FileSpreadsheet}
          title="No imported lists yet"
          description="Upload a CSV or Excel file. It is saved here as a list, and its prospects appear in Prospects without duplicates."
          action={
            canWrite && (
              <Link href="/prospects/import" className={cn(buttonVariants())}>
                <Upload /> Upload spreadsheet
              </Link>
            )
          }
        />
      ) : (
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>List</TableHead>
                <TableHead className="text-right">Contacts</TableHead>
                <TableHead className="hidden text-right sm:table-cell">Valid</TableHead>
                <TableHead className="hidden text-right md:table-cell">Duplicates</TableHead>
                <TableHead className="hidden lg:table-cell">Campaigns</TableHead>
                <TableHead className="hidden md:table-cell">Uploaded</TableHead>
                <TableHead className="w-10">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((l) => (
                <TableRow key={l.id}>
                  <TableCell className="max-w-72">
                    <Link href={`/prospects?batch_id=${l.id}`} className="block truncate font-medium hover:underline">
                      {l.name}
                    </Link>
                    <div className="truncate text-xs text-muted-foreground">
                      {l.original_filename ?? "—"} · {num(l.total_rows)} rows
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{num(l.contacts)}</TableCell>
                  <TableCell className="hidden text-right tabular-nums sm:table-cell" title="Not suppressed, unsubscribed or marked invalid">
                    {num(l.valid_contacts)}
                  </TableCell>
                  <TableCell
                    className="hidden text-right tabular-nums md:table-cell"
                    title={`${num(l.stats.updated ?? 0)} updated · ${num(l.stats.existing ?? 0)} already in Prospects · ${num(l.stats.duplicate_in_file ?? 0)} repeated in the file`}
                  >
                    {num(l.duplicates)}
                  </TableCell>
                  <TableCell className="hidden max-w-60 lg:table-cell">
                    {l.campaigns.length === 0 ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {l.campaigns.map((c) => (
                          <Link key={c.id} href={`/campaigns/${c.id}`} className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-xs hover:underline">
                            {c.name}
                            <StatusBadge status={c.status} className="h-4 px-1 text-[10px]" />
                          </Link>
                        ))}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground md:table-cell" title={dateTime(l.created_at)}>
                    {relTime(l.created_at)}
                    {l.uploaded_by && <div className="text-xs">by {l.uploaded_by}</div>}
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${l.name}`} />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem render={<Link href={`/prospects?batch_id=${l.id}`} />}>
                          <Users /> View contacts
                        </DropdownMenuItem>
                        {canWrite && (
                          <>
                            <DropdownMenuItem onClick={() => setAdding(l)}>
                              <Send /> Add to campaign
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => setRenaming(l)}>
                              <Pencil /> Rename
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => exportList(l)}>
                              <Download /> Export CSV
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onClick={() => setDeleting(l)}>
                              <Trash2 /> Delete list
                            </DropdownMenuItem>
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <AddListToCampaignDialog list={adding} onOpenChange={(o) => !o && setAdding(null)} />
      <RenameDialog list={renaming} onOpenChange={(o) => !o && setRenaming(null)} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete the list “${deleting?.name}”?`}
        description="Only the list is removed. Its prospects stay in Prospects, stay in any campaigns, and keep all their email history."
        confirmLabel="Delete list"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </>
  );
}
