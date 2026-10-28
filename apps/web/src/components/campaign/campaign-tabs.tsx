"use client";

import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, Field, NativeSelect, Pagination, StatusBadge } from "@/components/common";
import { ScheduleEditor } from "@/components/schedule-editor";
import { api, errorMessage } from "@/lib/api";
import { dateTime, humanize, relTime } from "@/lib/format";
import type { Campaign, CampaignMessage, EmailAccount, Page, Schedule } from "@/lib/types";

const PAGE_SIZE = 50;
const finished = (c: Campaign) => c.status === "completed" || c.status === "stopped";

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
  campaign_type: string;
  target_location: string;
  industry: string;
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
    campaign_type: c.campaign_type ?? "",
    target_location: c.target_location ?? "",
    industry: c.industry ?? "",
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
          campaign_type: draft.campaign_type,
          target_location: draft.target_location,
          industry: draft.industry,
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
          <Field label="Description" htmlFor="cs-desc">
            <Textarea id="cs-desc" rows={2} disabled={readOnly} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Campaign type" htmlFor="cs-type">
              <Input id="cs-type" maxLength={60} disabled={readOnly} value={draft.campaign_type} onChange={(e) => setDraft({ ...draft, campaign_type: e.target.value })} />
            </Field>
            <Field label="Target location" htmlFor="cs-loc">
              <Input id="cs-loc" maxLength={120} disabled={readOnly} value={draft.target_location} onChange={(e) => setDraft({ ...draft, target_location: e.target.value })} />
            </Field>
            <Field label="Industry" htmlFor="cs-ind">
              <Input id="cs-ind" maxLength={120} disabled={readOnly} value={draft.industry} onChange={(e) => setDraft({ ...draft, industry: e.target.value })} />
            </Field>
          </div>
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
        <AudienceCard campaign={campaign} readOnly={readOnly} />
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

function filterSummary(f: Campaign["audience_filter"]) {
  const parts: string[] = [];
  if (f.state) parts.push(`State ${f.state}`);
  if (f.city) parts.push(`City ${f.city}`);
  if (f.industry) parts.push(`Industry contains “${f.industry}”`);
  if (f.tag) parts.push(`Tag ${f.tag}`);
  if (f.batch_id) parts.push("From an imported list");
  if (f.lead_status) parts.push(`Status ${humanize(f.lead_status)}`);
  if (f.verification_status) parts.push(`Email ${f.verification_status}`);
  if (f.participation) parts.push(f.participation === "never" ? "Never in a campaign" : "Was in a campaign");
  for (const [k, v] of Object.entries(f.custom ?? {})) parts.push(`${k} = ${v}`);
  return parts.join(" · ") || "No filters";
}

/** Fixed vs dynamic audience: a dynamic campaign keeps adding prospects that match its saved filters. */
function AudienceCard({ campaign, readOnly }: { campaign: Campaign; readOnly: boolean }) {
  const qc = useQueryClient();
  const toFixed = useMutation({
    mutationFn: () => api<Campaign>(`/api/campaigns/${campaign.id}`, { method: "PATCH", body: { audience_mode: "fixed" } }),
    onSuccess: (c) => {
      qc.setQueryData(["campaign", campaign.id], c);
      toast.success("Audience is now fixed: no more prospects are added automatically");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dynamic = campaign.audience_mode === "dynamic";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Audience</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm">
        <p>
          <span className="font-medium">{dynamic ? "Dynamic" : "Fixed"}</span> —{" "}
          {dynamic
            ? "prospects matching these filters are added automatically while the campaign runs (removed prospects are never re-added):"
            : "only the prospects you add. Use Add contacts → Smart filters to make it dynamic."}
        </p>
        {dynamic && <p className="rounded-md bg-muted px-2 py-1.5 text-muted-foreground">{filterSummary(campaign.audience_filter)}</p>}
        {dynamic && !readOnly && (
          <div>
            <Button type="button" variant="outline" size="sm" disabled={toFixed.isPending} onClick={() => toFixed.mutate()}>
              Stop adding automatically
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
