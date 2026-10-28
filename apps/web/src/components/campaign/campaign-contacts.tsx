"use client";

import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, Download, Eye, FileSpreadsheet, Filter, History, MoveRight, Pause, Play, Plus, Search, Square, Tag as TagIcon, Upload, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, NativeSelect, Pagination, Spinner, StatusBadge } from "@/components/common";
import { LeadPreviewDialog } from "@/components/enroll-dialog";
import { EmailFrame } from "@/components/email-frame";
import type { AudienceTab } from "@/components/campaign/audience-picker";
import { stepLabel } from "@/components/campaign/labels";
import { api, download, errorMessage } from "@/lib/api";
import { dateTime, fullName, humanize, num, relTime } from "@/lib/format";
import type { Campaign, CampaignLead, HistoryItem, Page } from "@/lib/types";

const PAGE_SIZE = 50;
const STATUSES = ["active", "paused", "replied", "completed", "bounced", "unsubscribed", "stopped", "error", "removed"];

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Where this prospect is in the sequence, in plain words. */
function followUpStatus(r: CampaignLead, c: Campaign) {
  const next = c.steps[r.current_step];
  switch (r.status) {
    case "active":
      if (r.current_step === 0) return c.status === "active" ? "Initial email queued" : "Waiting for the campaign to start";
      if (!next) return "Finishing";
      return `${stepLabel(r.current_step + 1, next.name)}${next.status === "draft" ? " (draft)" : next.status === "paused" ? " (paused)" : ""}${
        r.next_send_at ? ` · ${relTime(r.next_send_at)}` : ""
      }`;
    case "paused":
      return "Paused by you";
    case "replied":
      return "Replied — follow-ups stopped";
    case "completed":
      return "All emails sent";
    case "unsubscribed":
      return "Unsubscribed";
    case "bounced":
      return "Bounced — stopped";
    case "stopped":
      return r.stop_reason === "stopped_by_user" ? "Follow-ups stopped by you" : `Stopped${r.stop_reason ? ` (${humanize(r.stop_reason)})` : ""}`;
    case "removed":
      return "Removed from campaign";
    default:
      return humanize(r.stop_reason ?? r.status);
  }
}

export function ContactHistoryDialog({ campaign, contact, onOpenChange }: { campaign: Campaign; contact: CampaignLead | null; onOpenChange: (o: boolean) => void }) {
  const q = useQuery({
    queryKey: ["contact-history", campaign.id, contact?.contact_id],
    queryFn: () => api<HistoryItem[]>(`/api/campaigns/${campaign.id}/contacts/${contact!.contact_id}/history`),
    enabled: !!contact,
  });
  return (
    <Dialog open={!!contact} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Email history · {contact ? fullName(contact) : ""}</DialogTitle>
          <DialogDescription>{contact?.email} in “{campaign.name}”. Every email sent and received is kept.</DialogDescription>
        </DialogHeader>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : q.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing sent yet.</p>
        ) : (
          <ol className="grid gap-3">
            {q.data.map((h, i) => (
              <li key={i} className={h.direction === "in" ? "rounded-lg border border-indigo-500/30 bg-indigo-500/5 p-3" : "rounded-lg border p-3"}>
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <StatusBadge status={h.direction === "out" ? "sent" : h.direction === "in" ? (h.kind === "bounce" ? "bounced" : "replied") : h.direction === "pending" ? "queued" : h.direction} />
                  {h.step && <span>{stepLabel(h.step, campaign.steps[h.step - 1]?.name)}</span>}
                  <span title={dateTime(h.at)}>{h.direction === "pending" ? `due ${relTime(h.at)}` : dateTime(h.at)}</span>
                  {h.intent && <StatusBadge status={h.intent} />}
                </div>
                {h.subject && <div className="text-sm font-medium">{h.subject}</div>}
                {h.error && <div className="text-xs text-destructive">{h.error}</div>}
                {h.body_html ? (
                  <EmailFrame html={h.body_html} title={`Email ${i + 1}`} autoHeight />
                ) : h.body_text ? (
                  <p className="whitespace-pre-wrap text-sm">{h.body_text}</p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}

function TargetCampaignDialog({ campaign, action, onPick, onOpenChange, pending }: {
  campaign: Campaign; action: "copy" | "move" | null; onPick: (id: string) => void; onOpenChange: (o: boolean) => void; pending: boolean;
}) {
  const list = useQuery({
    queryKey: ["campaigns", "open"],
    queryFn: () => api<Campaign[]>("/api/campaigns", { query: { status: "draft,scheduled,active,paused" } }),
    enabled: !!action,
  });
  const options = (list.data ?? []).filter((c) => c.id !== campaign.id);
  const [target, setTarget] = React.useState("");
  const chosen = target || options[0]?.id || "";
  return (
    <Dialog open={!!action} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{action === "move" ? "Move to another campaign" : "Copy to another campaign"}</DialogTitle>
          <DialogDescription>
            {action === "move"
              ? "They join the other campaign at its first email and leave this one. Emails already sent stay in their history."
              : "They join the other campaign at its first email and also stay in this one."}
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (chosen) onPick(chosen); }}>
          {options.length === 0 && list.data ? (
            <p className="text-sm text-muted-foreground">No other open campaign. Create one first.</p>
          ) : (
            <Field label="Campaign" htmlFor="tc-target">
              <NativeSelect id="tc-target" className="w-full" value={chosen} onChange={(e) => setTarget(e.target.value)}>
                {options.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} · {c.status}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!chosen || pending}>{action === "move" ? "Move" : "Copy"}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function CampaignContacts({ campaign, canWrite, onAdd }: { campaign: Campaign; canWrite: boolean; onAdd: (tab: AudienceTab) => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = React.useState("");
  const q = useDebounced(search.trim());
  const [status, setStatus] = React.useState("");
  const [state, setState] = React.useState("");
  const stateQ = useDebounced(state.trim());
  const [page, setPage] = React.useState(1);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [allMatching, setAllMatching] = React.useState(false);
  const [previewing, setPreviewing] = React.useState<string | null>(null);
  const [history, setHistory] = React.useState<CampaignLead | null>(null);
  const [target, setTarget] = React.useState<"copy" | "move" | null>(null);
  const [confirmRemove, setConfirmRemove] = React.useState(false);
  const [tagging, setTagging] = React.useState(false);
  const [tag, setTag] = React.useState("");

  const filterKey = JSON.stringify({ q, status, stateQ });
  const [prevKey, setPrevKey] = React.useState(filterKey);
  if (prevKey !== filterKey) {
    setPrevKey(filterKey);
    setPage(1);
    setSelected(new Set());
    setAllMatching(false);
  }

  const list = useQuery({
    queryKey: ["campaign-leads", campaign.id, q, status, stateQ, page],
    queryFn: () => api<Page<CampaignLead>>(`/api/campaigns/${campaign.id}/contacts`, { query: { q, status, state: stateQ, page, page_size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const allOnPage = items.length > 0 && items.every((r) => selected.has(r.contact_id));
  const count = allMatching ? total : selected.size;

  const bulk = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ message: string }>(`/api/campaigns/${campaign.id}/contacts/bulk`, {
        body: { ...body, ...(allMatching ? { all_matching: true, status: status || null, q: q || null } : { contact_ids: [...selected] }) },
      }),
    onSuccess: (r) => {
      toast.success(r.message);
      for (const k of ["campaign-leads", "campaign", "campaigns", "campaign-summary", "contacts", "contact-tags"]) qc.invalidateQueries({ queryKey: [k] });
      setSelected(new Set());
      setAllMatching(false);
      setTarget(null);
      setConfirmRemove(false);
      setTagging(false);
      setTag("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const one = useMutation({
    mutationFn: ({ action, id }: { action: string; id: string }) =>
      api<{ message: string }>(`/api/campaigns/${campaign.id}/contacts/bulk`, { body: { action, contact_ids: [id] } }),
    onSuccess: (r) => {
      toast.success(r.message);
      qc.invalidateQueries({ queryKey: ["campaign-leads", campaign.id] });
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  function toggle(id: string) {
    setAllMatching(false);
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  async function exportCsv() {
    try {
      await download(`/api/campaigns/${campaign.id}/contacts/export`, { status, q }, `${campaign.name}.csv`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  const finished = campaign.status === "completed" || campaign.status === "stopped";
  return (
    // minmax(0,1fr): the wide table scrolls inside its frame instead of widening the page
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" placeholder="Search name, email, business…" aria-label="Search campaign contacts" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <NativeSelect aria-label="Follow-up status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All (except removed)</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </NativeSelect>
        <Input aria-label="State" placeholder="State" className="w-20" value={state} onChange={(e) => setState(e.target.value)} />
        <div className="ml-auto flex flex-wrap gap-2">
          {canWrite && (
            <Button variant="outline" onClick={exportCsv}>
              <Download /> Export
            </Button>
          )}
          {canWrite && !finished && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button />}>
                <Plus /> Add contacts
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => onAdd("list")}>
                  <FileSpreadsheet /> From an imported list
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onAdd("filters")}>
                  <Filter /> With smart filters
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => onAdd("upload")}>
                  <Upload /> Upload a spreadsheet
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      {canWrite && count > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2 text-sm" role="region" aria-label="Bulk actions">
          <span className="font-medium">{num(count)} selected</span>
          {!allMatching && allOnPage && total > items.length && (
            <button type="button" className="text-signal hover:underline" onClick={() => setAllMatching(true)}>
              Select all {num(total)} matching
            </button>
          )}
          <div className="ml-auto flex flex-wrap gap-1.5">
            <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate({ action: "pause" })}>
              <Pause /> Pause
            </Button>
            <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate({ action: "resume" })}>
              <Play /> Resume
            </Button>
            <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => bulk.mutate({ action: "stop" })}>
              <Square /> Stop follow-ups
            </Button>
            <Button size="sm" variant="outline" onClick={() => setTarget("copy")}>
              <Copy /> Copy to…
            </Button>
            <Button size="sm" variant="outline" onClick={() => setTarget("move")}>
              <MoveRight /> Move to…
            </Button>
            <Button size="sm" variant="outline" onClick={() => setTagging(true)}>
              <TagIcon /> Tag
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirmRemove(true)}>
              <X /> Remove
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setSelected(new Set()); setAllMatching(false); }}>
              Clear
            </Button>
          </div>
        </div>
      )}

      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-40" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Users}
          title={q || status || stateQ ? "No contacts match" : "No contacts in this campaign yet"}
          description={q || status || stateQ ? undefined : "Add an imported list, use smart filters, or upload a spreadsheet."}
          action={canWrite && !finished && !(q || status || stateQ) && (
            <Button onClick={() => onAdd("list")}>
              <Plus /> Add contacts
            </Button>
          )}
        />
      ) : (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  {canWrite && (
                    <TableHead className="w-8">
                      <input type="checkbox" aria-label="Select all on this page" className="size-4 accent-[var(--primary)]" checked={allOnPage || allMatching}
                        onChange={(e) => { setAllMatching(false); setSelected(e.target.checked ? new Set(items.map((r) => r.contact_id)) : new Set()); }} />
                    </TableHead>
                  )}
                  <TableHead>Contact</TableHead>
                  <TableHead className="hidden lg:table-cell">Business</TableHead>
                  <TableHead className="hidden xl:table-cell">Phone</TableHead>
                  <TableHead className="hidden md:table-cell">State</TableHead>
                  <TableHead className="hidden sm:table-cell">Email status</TableHead>
                  <TableHead className="hidden md:table-cell">Last sent</TableHead>
                  <TableHead className="hidden lg:table-cell">Last reply</TableHead>
                  <TableHead>Follow-up status</TableHead>
                  <TableHead className="w-24"><span className="sr-only">Actions</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {items.map((r) => (
                  <TableRow key={r.contact_id} data-state={selected.has(r.contact_id) || allMatching ? "selected" : undefined}>
                    {canWrite && (
                      <TableCell>
                        <input type="checkbox" aria-label={`Select ${r.email}`} className="size-4 accent-[var(--primary)]" checked={allMatching || selected.has(r.contact_id)} onChange={() => toggle(r.contact_id)} />
                      </TableCell>
                    )}
                    <TableCell className="max-w-56">
                      <Link href={`/prospects/${r.contact_id}`} className="block truncate font-medium hover:underline">
                        {fullName(r)}
                      </Link>
                      <div className="truncate text-xs text-muted-foreground">{r.email}</div>
                    </TableCell>
                    <TableCell className="hidden max-w-48 truncate lg:table-cell">{r.company_name ?? "—"}</TableCell>
                    <TableCell className="hidden whitespace-nowrap text-muted-foreground xl:table-cell">{r.phone ?? "—"}</TableCell>
                    <TableCell className="hidden md:table-cell">{r.state ?? "—"}</TableCell>
                    <TableCell className="hidden sm:table-cell">
                      {r.suppressed ? <StatusBadge status="suppressed" /> : <StatusBadge status={r.verification_status} />}
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground md:table-cell" title={dateTime(r.last_sent_at)}>
                      {r.last_sent_at ? relTime(r.last_sent_at) : "—"}
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground lg:table-cell" title={dateTime(r.replied_at)}>
                      {r.replied_at ? relTime(r.replied_at) : "—"}
                    </TableCell>
                    <TableCell className="min-w-40">
                      <div className="flex items-center gap-1.5">
                        <StatusBadge status={r.status} />
                        <span className="text-xs text-muted-foreground">{r.current_step}/{r.total_steps}</span>
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">{followUpStatus(r, campaign)}</div>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-0.5">
                        <Button variant="ghost" size="icon-sm" aria-label={`Email history for ${r.email}`} onClick={() => setHistory(r)}>
                          <History />
                        </Button>
                        <Button variant="ghost" size="icon-sm" aria-label={`Preview emails for ${r.email}`} onClick={() => setPreviewing(r.contact_id)}>
                          <Eye />
                        </Button>
                        {canWrite && r.status === "active" && (
                          <Button variant="ghost" size="icon-sm" aria-label={`Pause ${r.email}`} onClick={() => one.mutate({ action: "pause", id: r.contact_id })}>
                            <Pause />
                          </Button>
                        )}
                        {canWrite && r.status === "paused" && (
                          <Button variant="ghost" size="icon-sm" aria-label={`Resume ${r.email}`} onClick={() => one.mutate({ action: "resume", id: r.contact_id })}>
                            <Play />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
        </>
      )}

      <LeadPreviewDialog campaignId={campaign.id} contactId={previewing} onOpenChange={(o) => !o && setPreviewing(null)} />
      <ContactHistoryDialog campaign={campaign} contact={history} onOpenChange={(o) => !o && setHistory(null)} />
      <TargetCampaignDialog campaign={campaign} action={target} pending={bulk.isPending} onOpenChange={(o) => !o && setTarget(null)}
        onPick={(id) => bulk.mutate({ action: target, target_campaign_id: id })} />
      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title={`Remove ${num(count)} contact${count === 1 ? "" : "s"} from this campaign?`}
        description="They get no more emails from this campaign. They stay in Prospects, and everything already sent or received is kept."
        confirmLabel="Remove from campaign"
        destructive
        pending={bulk.isPending}
        onConfirm={() => bulk.mutate({ action: "remove" })}
      />
      <Dialog open={tagging} onOpenChange={setTagging}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Tag {num(count)} contact{count === 1 ? "" : "s"}</DialogTitle>
          </DialogHeader>
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (tag.trim()) bulk.mutate({ action: "add_tag", tag: tag.trim() }); }}>
            <Field label="Tag" htmlFor="cc-tag">
              <Input id="cc-tag" autoFocus maxLength={80} value={tag} onChange={(e) => setTag(e.target.value)} placeholder="texas-q4" />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setTagging(false)}>Cancel</Button>
              <Button type="submit" disabled={!tag.trim() || bulk.isPending}>Add tag</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
