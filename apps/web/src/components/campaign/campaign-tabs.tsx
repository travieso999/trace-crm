"use client";

import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Eye, Inbox, Plus, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, Field, NativeSelect, Pagination, StatusBadge } from "@/components/common";
import { ScheduleEditor } from "@/components/schedule-editor";
import { LeadPreviewDialog } from "@/components/enroll-dialog";
import { api, errorMessage } from "@/lib/api";
import { dateTime, fullName, humanize, num, relTime } from "@/lib/format";
import { LEAD_STATUSES, type Campaign, type CampaignLead, type CampaignMessage, type EmailAccount, type EnrollResult, type Page, type Schedule, type Tag } from "@/lib/types";

const PAGE_SIZE = 50;
const finished = (c: Campaign) => c.status === "completed" || c.status === "stopped";

function AddLeadsDialog({ campaign, open, onOpenChange }: { campaign: Campaign; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const tags = useQuery({ queryKey: ["contact-tags"], queryFn: () => api<Tag[]>("/api/contacts/tags"), enabled: open });
  const [f, setF] = React.useState({ tag: "", lead_status: "new_lead", state: "", verification_status: "" });
  const match = useQuery({
    queryKey: ["contacts-count", f],
    queryFn: () => api<Page<unknown>>("/api/contacts", { query: { ...f, page_size: 1 } }),
    enabled: open,
  });
  const enroll = useMutation({
    mutationFn: () =>
      api<EnrollResult & { skipped_missing_fields?: number }>(`/api/campaigns/${campaign.id}/contacts`, { body: { ...f, only_ready: true } }),
    onSuccess: (r) => {
      const missing = r.skipped_missing_fields ?? 0;
      const skipped = r.skipped_existing + r.skipped_suppressed + r.skipped_invalid + missing;
      toast.success(`${num(r.enrolled)} prospect${r.enrolled === 1 ? "" : "s"} enrolled`, {
        description: skipped
          ? `Skipped ${r.skipped_existing} already enrolled, ${r.skipped_suppressed} suppressed, ${r.skipped_invalid} invalid or closed, ${missing} missing merge fields`
          : undefined,
      });
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
      qc.invalidateQueries({ queryKey: ["campaign-leads", campaign.id] });
      qc.invalidateQueries({ queryKey: ["campaign-preflight", campaign.id] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add prospects</DialogTitle>
          <DialogDescription>
            Enroll every prospect matching these filters. To pick individual prospects, select them on the{" "}
            <Link href="/prospects" className="text-signal hover:underline">
              Prospects
            </Link>{" "}
            page and use “Add to campaign”.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            enroll.mutate();
          }}
        >
          <Field label="Tag" htmlFor="al-tag">
            <NativeSelect id="al-tag" value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value })}>
              <option value="">Any tag</option>
              {(tags.data ?? []).map((t) => (
                <option key={t.id} value={t.name}>
                  {t.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Prospect status" htmlFor="al-status">
              <NativeSelect id="al-status" value={f.lead_status} onChange={(e) => setF({ ...f, lead_status: e.target.value })}>
                <option value="">Any</option>
                {LEAD_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {humanize(s)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="State" htmlFor="al-state">
              <Input id="al-state" placeholder="TX, FL" value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })} />
            </Field>
          </div>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {match.data ? `${num(match.data.total)} matching prospect${match.data.total === 1 ? "" : "s"}. Suppressed, invalid and closed prospects are skipped.` : " "}
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={enroll.isPending || !match.data?.total || !(f.tag || f.lead_status || f.state)}>
              Enroll prospects
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function CampaignLeads({ campaign, canWrite }: { campaign: Campaign; canWrite: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [adding, setAdding] = React.useState(false);
  const [previewing, setPreviewing] = React.useState<string | null>(null);
  const list = useQuery({
    queryKey: ["campaign-leads", campaign.id, status, page],
    queryFn: () => api<Page<CampaignLead>>(`/api/campaigns/${campaign.id}/contacts`, { query: { status, page, page_size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
  });
  const remove = useMutation({
    mutationFn: (contactId: string) => api(`/api/campaigns/${campaign.id}/contacts/${contactId}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Prospect removed; pending sends cancelled");
      qc.invalidateQueries({ queryKey: ["campaign-leads", campaign.id] });
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const items = list.data?.items ?? [];
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <NativeSelect aria-label="Enrollment status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All enrollments</option>
          {["active", "replied", "completed", "bounced", "unsubscribed", "stopped", "removed", "error"].map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </NativeSelect>
        {canWrite && !finished(campaign) && (
          <Button onClick={() => setAdding(true)}>
            <Plus /> Add prospects
          </Button>
        )}
      </div>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-40" />
      ) : items.length === 0 ? (
        <EmptyState icon={Users} title={status ? "No prospects with this status" : "No prospects enrolled"} description={status ? undefined : "Add prospects to start the sequence for them."} />
      ) : (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Prospect</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden sm:table-cell">Progress</TableHead>
                  <TableHead className="hidden md:table-cell">Next send</TableHead>
                  <TableHead className="hidden lg:table-cell">Mailbox</TableHead>
                  <TableHead className="w-20"><span className="sr-only">Actions</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {items.map((r) => (
                  <TableRow key={r.contact_id}>
                    <TableCell className="max-w-64">
                      <Link href={`/prospects/${r.contact_id}`} className="block truncate font-medium hover:underline">
                        {fullName(r)}
                      </Link>
                      <div className="truncate text-xs text-muted-foreground">{r.email}</div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={r.status} />
                      {r.stop_reason && r.status !== "active" && (
                        <div className="mt-0.5 text-xs text-muted-foreground">{humanize(r.stop_reason)}</div>
                      )}
                    </TableCell>
                    <TableCell className="hidden tabular-nums sm:table-cell">
                      {r.current_step} / {r.total_steps} sent
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground md:table-cell" title={dateTime(r.next_send_at)}>
                      {r.status === "active" ? (r.next_send_at ? relTime(r.next_send_at) : campaign.status === "active" ? "Queued" : "When started") : "—"}
                    </TableCell>
                    <TableCell className="hidden truncate text-muted-foreground lg:table-cell">{r.mailbox ?? "—"}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-0.5">
                        <Button variant="ghost" size="icon-sm" aria-label={`Preview emails for ${r.email}`} onClick={() => setPreviewing(r.contact_id)}>
                          <Eye />
                        </Button>
                        {canWrite && r.status === "active" && (
                          <Button variant="ghost" size="icon-sm" aria-label={`Remove ${r.email} from campaign`} onClick={() => remove.mutate(r.contact_id)}>
                            <X />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        </>
      )}
      <AddLeadsDialog campaign={campaign} open={adding} onOpenChange={setAdding} />
      <LeadPreviewDialog campaignId={campaign.id} contactId={previewing} onOpenChange={(o) => !o && setPreviewing(null)} />
    </div>
  );
}

function SimulateDialog({ message, onOpenChange }: { message: CampaignMessage | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = React.useState("reply");
  const [body, setBody] = React.useState("Sounds good, let's talk next week.");
  const sim = useMutation({
    mutationFn: () =>
      api<{ processed: number }>(`/api/email-accounts/${message!.mailbox_id}/sandbox/simulate`, {
        body: { sent_message_id: message!.sent_message_id, kind, body },
      }),
    onSuccess: (r) => {
      toast.success(r.processed ? "Simulated message received and processed" : "Nothing new to process");
      for (const k of ["campaign", "campaign-leads", "campaign-messages", "inbox", "contacts", "opportunities", "dashboard"]) qc.invalidateQueries({ queryKey: [k] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!message} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Simulate inbound mail</DialogTitle>
          <DialogDescription>
            Sandbox only. Drops a message from {message?.to} into the sandbox inbox and runs the normal sync, so you can test reply handling,
            sequence stopping and the inbox without real email.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            sim.mutate();
          }}
        >
          <Field label="Type" htmlFor="sim-kind">
            <NativeSelect id="sim-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="reply">Reply</option>
              <option value="auto_reply">Out-of-office auto-reply</option>
              <option value="bounce">Bounce (address doesn’t exist)</option>
            </NativeSelect>
          </Field>
          {kind !== "bounce" && (
            <Field label="Message" htmlFor="sim-body" hint="Try “not interested” or “please remove me” to see classification.">
              <Textarea id="sim-body" rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
            </Field>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={sim.isPending}>
              Simulate
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function CampaignMessages({ campaign, canSimulate }: { campaign: Campaign; canSimulate?: boolean }) {
  const [status, setStatus] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [simulating, setSimulating] = React.useState<CampaignMessage | null>(null);
  const list = useQuery({
    queryKey: ["campaign-messages", campaign.id, status, page],
    queryFn: () => api<Page<CampaignMessage>>(`/api/campaigns/${campaign.id}/messages`, { query: { status, page, page_size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
    refetchInterval: campaign.status === "active" ? 30_000 : false,
  });
  const items = list.data?.items ?? [];
  return (
    <div className="grid gap-3">
      <NativeSelect aria-label="Message status" className="w-fit" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
        <option value="">All messages</option>
        {["queued", "sending", "sent", "failed", "cancelled", "unknown"].map((s) => (
          <option key={s} value={s}>
            {humanize(s)}
          </option>
        ))}
      </NativeSelect>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-40" />
      ) : items.length === 0 ? (
        <EmptyState icon={Inbox} title="No messages yet" description="Messages appear here once the campaign is running and sends are queued." />
      ) : (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>To</TableHead>
                  <TableHead>Step</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden md:table-cell">Subject</TableHead>
                  <TableHead className="hidden sm:table-cell">When</TableHead>
                  {canSimulate && <TableHead className="w-24"><span className="sr-only">Test tools</span></TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="max-w-48 truncate">{m.to}</TableCell>
                    <TableCell className="tabular-nums">{m.step}</TableCell>
                    <TableCell>
                      <StatusBadge status={m.status} />
                      {m.last_error && <div className="mt-0.5 max-w-56 truncate text-xs text-muted-foreground" title={m.last_error}>{m.last_error}</div>}
                    </TableCell>
                    <TableCell className="hidden max-w-72 truncate md:table-cell">{m.subject ?? "—"}</TableCell>
                    <TableCell className="hidden text-muted-foreground sm:table-cell" title={dateTime(m.sent_at ?? m.due_at)}>
                      {m.sent_at ? `Sent ${relTime(m.sent_at)}` : `Due ${relTime(m.due_at)}`}
                    </TableCell>
                    {canSimulate && (
                      <TableCell>
                        {m.status === "sent" && m.mailbox_provider === "sandbox" && m.sent_message_id && (
                          <Button variant="ghost" size="sm" onClick={() => setSimulating(m)}>
                            Simulate…
                          </Button>
                        )}
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        </>
      )}
      <SimulateDialog message={simulating} onOpenChange={(o) => !o && setSimulating(null)} />
    </div>
  );
}

type SettingsDraft = {
  name: string;
  description: string;
  daily_limit: number;
  schedule: Schedule;
  mailbox_ids: string[];
  missing_variable_policy: "block" | "fallback";
  fallbacks: string;
  stop_on_auto_reply: boolean;
  start_at: string;
};

function toDraft(c: Campaign): SettingsDraft {
  return {
    name: c.name,
    description: c.description ?? "",
    daily_limit: c.daily_limit,
    schedule: c.schedule,
    mailbox_ids: c.mailbox_ids,
    missing_variable_policy: c.missing_variable_policy,
    fallbacks: Object.entries(c.variable_fallbacks ?? {}).map(([k, v]) => `${k}=${v}`).join("\n"),
    stop_on_auto_reply: c.stop_on_auto_reply,
    // datetime-local wants local time without zone
    start_at: c.start_at ? new Date(new Date(c.start_at).getTime() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : "",
  };
}

export function CampaignSettings({ campaign, canWrite }: { campaign: Campaign; canWrite: boolean }) {
  const qc = useQueryClient();
  const readOnly = !canWrite || finished(campaign);
  const boxes = useQuery({ queryKey: ["email-accounts"], queryFn: () => api<EmailAccount[]>("/api/email-accounts") });
  const [draft, setDraft] = React.useState(() => toDraft(campaign));
  const [source, setSource] = React.useState(campaign.updated_at);
  if (source !== campaign.updated_at) {
    setSource(campaign.updated_at);
    setDraft(toDraft(campaign));
  }
  const save = useMutation({
    mutationFn: () => {
      const fallbacks = Object.fromEntries(
        draft.fallbacks
          .split("\n")
          .map((l) => l.split("="))
          .filter(([k, ...v]) => k.trim() && v.join("=").trim())
          .map(([k, ...v]) => [k.trim(), v.join("=").trim()]),
      );
      return api<Campaign>(`/api/campaigns/${campaign.id}`, {
        method: "PATCH",
        body: {
          name: draft.name,
          description: draft.description || null,
          daily_limit: draft.daily_limit,
          schedule: draft.schedule,
          mailbox_ids: draft.mailbox_ids,
          missing_variable_policy: draft.missing_variable_policy,
          variable_fallbacks: fallbacks,
          stop_on_auto_reply: draft.stop_on_auto_reply,
          start_at: draft.start_at ? new Date(draft.start_at).toISOString() : null,
        },
      });
    },
    onSuccess: (c) => {
      qc.setQueryData(["campaign", campaign.id], c);
      qc.invalidateQueries({ queryKey: ["campaign-preflight", campaign.id] });
      toast.success("Campaign settings saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const available = (boxes.data ?? []).filter((b) => b.status !== "disconnected");
  return (
    <form
      className="grid gap-4 lg:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Card>
        <CardHeader>
          <CardTitle>General</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Field label="Name" htmlFor="cs-name">
            <Input id="cs-name" maxLength={200} disabled={readOnly} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </Field>
          <Field label="Notes" htmlFor="cs-desc">
            <Textarea id="cs-desc" rows={2} disabled={readOnly} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Daily limit (campaign)" htmlFor="cs-limit" hint="Mailbox limits also apply.">
              <Input id="cs-limit" type="number" min={1} max={1000} disabled={readOnly} value={draft.daily_limit}
                onChange={(e) => setDraft({ ...draft, daily_limit: Number(e.target.value) })} />
            </Field>
            <Field label="Start at (optional)" htmlFor="cs-start" hint="Leave empty to start immediately.">
              <Input id="cs-start" type="datetime-local" disabled={readOnly} value={draft.start_at} onChange={(e) => setDraft({ ...draft, start_at: e.target.value })} />
            </Field>
          </div>
          <fieldset className="grid gap-1.5" disabled={readOnly}>
            <legend className="mb-1.5 text-sm font-medium">Send from</legend>
            {available.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No mailboxes yet.{" "}
                <Link href="/email-accounts" className="text-signal hover:underline">
                  Connect one
                </Link>
                .
              </p>
            ) : (
              available.map((b) => (
                <label key={b.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--primary)]"
                    checked={draft.mailbox_ids.includes(b.id)}
                    onChange={(e) =>
                      setDraft({ ...draft, mailbox_ids: e.target.checked ? [...draft.mailbox_ids, b.id] : draft.mailbox_ids.filter((x) => x !== b.id) })
                    }
                  />
                  <span className="truncate">{b.email}</span>
                  <StatusBadge status={b.status} />
                </label>
              ))
            )}
            <p className="text-xs text-muted-foreground">Prospects are spread across the selected mailboxes; each prospect stays on one mailbox so follow-ups thread.</p>
          </fieldset>
        </CardContent>
      </Card>
      <div className="grid content-start gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Sending window</CardTitle>
          </CardHeader>
          <CardContent>
            <ScheduleEditor value={draft.schedule} disabled={readOnly} showRecipientTz onChange={(schedule) => setDraft({ ...draft, schedule })} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Safety</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3">
            <Field label="When a prospect is missing a variable" htmlFor="cs-policy">
              <NativeSelect id="cs-policy" disabled={readOnly} value={draft.missing_variable_policy}
                onChange={(e) => setDraft({ ...draft, missing_variable_policy: e.target.value as "block" | "fallback" })}>
                <option value="block">Don’t send to that prospect (recommended)</option>
                <option value="fallback">Use the fallback values below</option>
              </NativeSelect>
            </Field>
            {draft.missing_variable_policy === "fallback" && (
              <Field label="Fallback values" htmlFor="cs-fallbacks" hint="One per line: variable=value, e.g. first_name=there">
                <Textarea id="cs-fallbacks" rows={3} className="font-mono text-[13px]" disabled={readOnly} value={draft.fallbacks}
                  onChange={(e) => setDraft({ ...draft, fallbacks: e.target.value })} />
              </Field>
            )}
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]" disabled={readOnly} checked={draft.stop_on_auto_reply}
                onChange={(e) => setDraft({ ...draft, stop_on_auto_reply: e.target.checked })} />
              <span>
                Stop the sequence on auto-replies
                <span className="block text-xs text-muted-foreground">Real replies, bounces and unsubscribes always stop it.</span>
              </span>
            </label>
          </CardContent>
        </Card>
        {!readOnly && (
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setDraft(toDraft(campaign))}>
              Discard
            </Button>
            <Button type="submit" disabled={save.isPending || !draft.name.trim()}>
              Save settings
            </Button>
          </div>
        )}
      </div>
    </form>
  );
}
