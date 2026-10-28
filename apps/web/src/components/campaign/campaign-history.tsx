"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ChevronRight, Info, Mail } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, Spinner, StatusBadge } from "@/components/common";
import { CampaignMessages } from "@/components/campaign/campaign-tabs";
import { stepLabel } from "@/components/campaign/labels";
import { api } from "@/lib/api";
import { dateTime, fullName, num, pct, relTime, shortDate } from "@/lib/format";
import type { Campaign, CampaignAnalytics, StepMessage, StepReport } from "@/lib/types";

const STATE_LABEL: Record<StepReport["state"], string> = {
  draft: "Draft",
  paused: "Paused",
  sent: "Sent",
  sending: "Sending",
  scheduled: "Scheduled",
  waiting: "Waiting for earlier emails",
};

function when(r: StepReport) {
  if (r.state === "sent") return `Sent ${shortDate(r.first_sent_at)}${r.last_sent_at && shortDate(r.last_sent_at) !== shortDate(r.first_sent_at) ? `–${shortDate(r.last_sent_at)}` : ""}`;
  if (r.state === "sending") return `Sending since ${shortDate(r.first_sent_at)}${r.next_send_at ? ` · next ${relTime(r.next_send_at)}` : ""}`;
  if (r.state === "scheduled") return r.next_send_at ? `Scheduled ${dateTime(r.next_send_at)}` : "Queued";
  if (r.send_at) return `Not before ${dateTime(r.send_at)}`;
  return r.position === 1 ? "When the campaign starts" : `${r.delay_days} ${r.business_days ? "business " : ""}day${r.delay_days === 1 ? "" : "s"} after the previous email`;
}

function StepDialog({ campaign, step, onOpenChange }: { campaign: Campaign; step: StepReport | null; onOpenChange: (o: boolean) => void }) {
  const q = useQuery({
    queryKey: ["step-messages", campaign.id, step?.step_id],
    queryFn: () => api<StepMessage[]>(`/api/campaigns/${campaign.id}/steps/${step!.step_id}/messages`),
    enabled: !!step,
  });
  if (!step) return null;
  const tiles: [string, number][] = [
    ["Recipients", step.recipients],
    ["Sent", step.sent],
    ["Delivered*", step.delivered],
    ["Failed", step.failed],
    ["Bounced", step.bounced],
    ["Replied", step.replied],
    ["Unsubscribed", step.unsubscribed],
  ];
  return (
    <Dialog open={!!step} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{stepLabel(step.position, step.name)}</DialogTitle>
          <DialogDescription>{when(step)}</DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-4 gap-2 sm:grid-cols-7">
          {tiles.map(([l, n]) => (
            <div key={l} className="rounded-lg border p-2">
              <dt className="text-[11px] text-muted-foreground">{l}</dt>
              <dd className="text-lg font-semibold tabular-nums">{num(n)}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs text-muted-foreground">*Sent and not bounced. Mail servers don’t report inbox delivery or opens.</p>
        <div className="rounded-lg border p-3">
          <div className="text-xs text-muted-foreground">Subject</div>
          <div className="font-medium">{step.subject || (step.position > 1 ? "Re: (same thread as the first email)" : "—")}</div>
          <div className="mt-2 text-xs text-muted-foreground">Content</div>
          <p className="whitespace-pre-wrap text-sm">{step.body || "—"}</p>
        </div>
        <h3 className="text-sm font-medium">Recipients</h3>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : q.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">Not sent to anyone yet.</p>
        ) : (
          <div className="max-h-80 overflow-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Contact</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead className="hidden sm:table-cell">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {q.data.map((m) => (
                  <TableRow key={m.contact_id}>
                    <TableCell className="max-w-56">
                      <Link href={`/prospects/${m.contact_id}`} className="block truncate hover:underline">
                        {fullName(m)}
                      </Link>
                      <div className="truncate text-xs text-muted-foreground">{m.email}</div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={m.status} />
                      {m.replied_at && <StatusBadge status="replied" className="ml-1" />}
                      {m.error && <div className="max-w-56 truncate text-xs text-muted-foreground" title={m.error}>{m.error}</div>}
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground sm:table-cell" title={dateTime(m.sent_at ?? m.due_at)}>
                      {m.sent_at ? relTime(m.sent_at) : `due ${relTime(m.due_at)}`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function CampaignHistory({ campaign, canSimulate }: { campaign: Campaign; canSimulate?: boolean }) {
  const [open, setOpen] = React.useState<StepReport | null>(null);
  const q = useQuery({
    queryKey: ["steps-report", campaign.id],
    queryFn: () => api<StepReport[]>(`/api/campaigns/${campaign.id}/steps-report`),
    refetchInterval: campaign.status === "active" ? 30_000 : false,
  });
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Emails in this campaign</CardTitle>
        </CardHeader>
        <CardContent>
          {q.isError ? (
            <ErrorState error={q.error} onRetry={() => q.refetch()} />
          ) : q.isPending ? (
            <Skeleton className="h-32" />
          ) : q.data.length === 0 ? (
            <EmptyState icon={Mail} title="No emails yet" description="Write the first email in the Email Sequence tab." />
          ) : (
            <ol className="grid gap-2">
              {q.data.map((r) => (
                <li key={r.step_id}>
                  <button
                    type="button"
                    onClick={() => setOpen(r)}
                    className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-3 py-2.5 text-left outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{stepLabel(r.position, r.name)}</span>
                        <span className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-xs">{STATE_LABEL[r.state]}</span>
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        <CalendarClock className="mr-1 inline size-3" aria-hidden />
                        {when(r)}
                        {r.subject ? ` · ${r.subject}` : ""}
                      </div>
                    </div>
                    <dl className="flex gap-4 text-xs tabular-nums">
                      {(
                        [
                          ["Sent", r.sent],
                          ["Waiting", r.waiting],
                          ["Failed", r.failed],
                          ["Bounced", r.bounced],
                          ["Replied", r.replied],
                          ["Unsub.", r.unsubscribed],
                        ] as const
                      ).map(([l, n]) => (
                        <div key={l} className="text-center">
                          <dt className="text-muted-foreground">{l}</dt>
                          <dd className="font-semibold">{num(n)}</dd>
                        </div>
                      ))}
                    </dl>
                    <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                  </button>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Every message</CardTitle>
        </CardHeader>
        <CardContent>
          <CampaignMessages campaign={campaign} canSimulate={canSimulate} />
        </CardContent>
      </Card>
      <StepDialog campaign={campaign} step={open} onOpenChange={(o) => !o && setOpen(null)} />
    </div>
  );
}

function Tile({ label, value, sub, title }: { label: string; value: string; sub?: string; title?: string }) {
  return (
    <Card size="sm" title={title}>
      <CardContent>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

const rate = (n: number | null) => (n === null ? "—" : pct(n));

export function CampaignAnalyticsTab({ campaign }: { campaign: Campaign }) {
  const q = useQuery({
    queryKey: ["campaign-analytics", campaign.id],
    queryFn: () => api<CampaignAnalytics>(`/api/campaigns/${campaign.id}/analytics`),
    refetchInterval: campaign.status === "active" ? 30_000 : false,
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isPending) return <Skeleton className="h-64" />;
  const a = q.data;
  const d = a.definitions;
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Total contacts" value={num(a.total_contacts)} sub={`${num(a.contacts_emailed)} emailed so far`} />
        <Tile label="Emails sent" value={num(a.emails_sent)} sub={`${num(a.followups_sent)} follow-ups · ${num(a.queued)} queued`} title={d.emails_sent} />
        <Tile label="Delivered" value={num(a.delivered)} sub={`${rate(a.rates.delivery_rate)} delivery rate`} title={`${d.delivered}. ${d.delivery_rate}`} />
        <Tile label="Replies" value={num(a.replies)} sub={`${rate(a.rates.reply_rate)} reply rate`} title={d.reply_rate} />
        <Tile label="Positive replies" value={num(a.positive_replies)} sub={`${rate(a.rates.positive_reply_rate)} of contacts emailed`} title={d.positive_reply_rate} />
        <Tile label="Bounced" value={num(a.bounced)} sub={`${rate(a.rates.bounce_rate)} bounce rate`} title={d.bounce_rate} />
        <Tile label="Failed" value={num(a.failed)} sub="Not sent (e.g. missing data)" />
        <Tile label="Unsubscribes" value={num(a.unsubscribes)} sub={`${rate(a.rates.unsubscribe_rate)} of contacts emailed`} title={d.unsubscribe_rate} />
        <Tile label="Awaiting follow-up" value={num(a.awaiting_followup)} sub={`${num(a.not_started)} not emailed yet`} />
        <Tile label="Completed sequence" value={num(a.completed_sequence)} sub="Received every email" />
        <Tile label="Paused" value={num(a.paused)} sub="Paused by you" />
        <Tile label="Removed" value={num(a.removed)} sub="Still in Prospects" />
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Info className="size-4 text-muted-foreground" aria-hidden /> How these numbers are calculated
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-1.5 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-4">
            {Object.entries(d).map(([k, v]) => (
              <React.Fragment key={k}>
                <dt className="font-medium capitalize">{k.replace(/_/g, " ")}</dt>
                <dd className="text-muted-foreground">{v}</dd>
              </React.Fragment>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
