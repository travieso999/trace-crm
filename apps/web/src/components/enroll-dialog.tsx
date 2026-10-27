"use client";

import * as React from "react";
import Link from "next/link";
import { cn } from "cn";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Eye } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Field, NativeSelect, Spinner } from "@/components/common";
import { EmailFrame } from "@/components/email-frame";
import { api, errorMessage } from "@/lib/api";
import { fullName, num } from "@/lib/format";
import type { Campaign, EnrollDryRun, EnrollStatus, EnrollResult, LeadPreview as LeadPreviewData } from "@/lib/types";

const TONE = {
  ok: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  warn: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  bad: "bg-red-500/12 text-red-700 dark:text-red-400",
  muted: "bg-muted text-muted-foreground",
};

export const ENROLL_STATUS: Record<EnrollStatus, { label: string; tone: string }> = {
  ready: { label: "Ready", tone: TONE.ok },
  missing_fields: { label: "Missing merge fields", tone: TONE.warn },
  already_enrolled: { label: "Already in campaign", tone: TONE.muted },
  suppressed: { label: "Suppressed", tone: TONE.bad },
  invalid_email: { label: "Invalid email", tone: TONE.bad },
  closed_stage: { label: "Closed (won/lost/not interested)", tone: TONE.muted },
  not_found: { label: "Not found", tone: TONE.bad },
};

function EnrollBadge({ status }: { status: EnrollStatus }) {
  return (
    <span className={cn("inline-flex h-5 items-center rounded-md px-1.5 text-xs font-medium", ENROLL_STATUS[status].tone)}>
      {ENROLL_STATUS[status].label}
    </span>
  );
}

/** Every step of a campaign rendered for one lead, exactly as it would be sent (read-only). */
export function LeadPreview({ campaignId, contactId }: { campaignId: string; contactId: string }) {
  const q = useQuery({
    queryKey: ["lead-preview", campaignId, contactId],
    queryFn: () => api<LeadPreviewData>(`/api/campaigns/${campaignId}/preview/${contactId}`),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <p className="text-sm text-destructive">{errorMessage(q.error)}</p>;
  const { contact, steps, footer_problem } = q.data;
  return (
    <div className="grid gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{fullName({ ...contact, email: contact.email ?? undefined })}</span>
        <span className="text-muted-foreground">{contact.email}</span>
        <EnrollBadge status={contact.status} />
      </div>
      {footer_problem && (
        <p className="rounded-md bg-destructive/10 px-2 py-1.5 text-destructive" role="status">
          {footer_problem}
        </p>
      )}
      {steps.map((s) => (
        <section key={s.position} className="grid gap-1.5 rounded-lg border p-3" aria-label={`Step ${s.position}`}>
          <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Step {s.position}</span>
            <span>
              {s.position === 1 ? "Sent first" : `${s.delay_days} ${s.business_days ? "business " : ""}day(s) later`}
              {s.threaded && " · same thread"}
            </span>
          </div>
          {s.ok ? (
            <>
              <div className="font-medium">{s.subject}</div>
              <EmailFrame key={s.html} html={s.html!} title={`Step ${s.position} preview`} autoHeight />
            </>
          ) : (
            <p className="rounded-md bg-amber-500/12 px-2 py-1.5 text-amber-800 dark:text-amber-300" role="status">
              Not sendable: missing {s.missing.map((m) => `{{${m}}}`).join(", ")}. This step would be blocked for this prospect — never sent with a
              blank.
            </p>
          )}
        </section>
      ))}
    </div>
  );
}

export function LeadPreviewDialog({ campaignId, contactId, onOpenChange }: { campaignId: string; contactId: string | null; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={!!contactId} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Personalized preview</DialogTitle>
          <DialogDescription>What this prospect would receive. Nothing is sent from this view.</DialogDescription>
        </DialogHeader>
        {contactId && <LeadPreview campaignId={campaignId} contactId={contactId} />}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Add leads to a campaign with a dry run first: pick a campaign → see who is ready / skipped / missing merge fields →
 * preview any lead → enroll only the ready ones. Enrolling never starts a campaign.
 */
export function EnrollDialog({ contactIds, open, onOpenChange, onDone }: { contactIds: string[]; open: boolean; onOpenChange: (o: boolean) => void; onDone?: () => void }) {
  const qc = useQueryClient();
  const campaigns = useQuery({ queryKey: ["campaigns", "", false], queryFn: () => api<Campaign[]>("/api/campaigns"), enabled: open });
  const usable = (campaigns.data ?? []).filter((c) => !["completed", "stopped"].includes(c.status));
  const [campaignId, setCampaignId] = React.useState("");
  const [previewing, setPreviewing] = React.useState<string | null>(null);
  const [filter, setFilter] = React.useState<"all" | "attention">("all");
  const campaign = usable.find((c) => c.id === campaignId);

  const dry = useQuery({
    queryKey: ["enroll-dry-run", campaignId, contactIds],
    queryFn: () => api<EnrollDryRun>(`/api/campaigns/${campaignId}/contacts`, { body: { contact_ids: contactIds, dry_run: true } }),
    enabled: open && !!campaignId && contactIds.length > 0,
  });
  const enroll = useMutation({
    mutationFn: () => api<EnrollResult & { skipped_missing_fields?: number }>(`/api/campaigns/${campaignId}/contacts`, { body: { contact_ids: contactIds, only_ready: true } }),
    onSuccess: (r) => {
      toast.success(`${num(r.enrolled)} prospect${r.enrolled === 1 ? "" : "s"} added to “${campaign?.name}”`, {
        description:
          campaign?.status === "draft"
            ? "The campaign is still a draft: nothing is sent until you start it."
            : "These prospects are queued under the campaign’s schedule and limits.",
      });
      for (const k of ["campaigns", "campaign", "campaign-leads", "campaign-preflight", "contacts"]) qc.invalidateQueries({ queryKey: [k] });
      onOpenChange(false);
      onDone?.();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const leads = dry.data?.leads ?? [];
  const shown = filter === "all" ? leads : leads.filter((l) => l.status !== "ready");
  const ready = dry.data?.summary.ready ?? 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setPreviewing(null);
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Add {num(contactIds.length)} prospect{contactIds.length === 1 ? "" : "s"} to a campaign</DialogTitle>
          <DialogDescription>A dry run checks every prospect first. Only ready prospects are added; nothing is sent from here.</DialogDescription>
        </DialogHeader>
        {previewing ? (
          <div className="grid gap-3">
            <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => setPreviewing(null)}>
              <ArrowLeft /> Back to report
            </Button>
            <LeadPreview campaignId={campaignId} contactId={previewing} />
          </div>
        ) : (
          <div className="grid gap-4">
            <Field label="Campaign" htmlFor="enroll-campaign">
              <NativeSelect id="enroll-campaign" value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
                <option value="">Choose a campaign…</option>
                {usable.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.status})
                  </option>
                ))}
              </NativeSelect>
            </Field>
            {usable.length === 0 && campaigns.isSuccess && (
              <p className="text-sm text-muted-foreground">
                No open campaigns.{" "}
                <Link href="/campaigns" className="text-signal hover:underline">
                  Create one
                </Link>{" "}
                first.
              </p>
            )}
            {campaign && campaign.status !== "draft" && (
              <p className="rounded-md bg-amber-500/12 px-2 py-1.5 text-sm text-amber-800 dark:text-amber-300" role="status">
                This campaign is {campaign.status}. Prospects you add will be scheduled under its sending window and limits (suppression and unsubscribe rules
                still apply).
              </p>
            )}
            {campaignId && (dry.isPending ? (
              <Spinner />
            ) : dry.isError ? (
              <p className="text-sm text-destructive">{errorMessage(dry.error)}</p>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {(["ready", "missing_fields", "already_enrolled", "suppressed"] as EnrollStatus[]).map((s) => (
                    <div key={s} className="rounded-lg border p-2">
                      <dt className="text-xs text-muted-foreground">{ENROLL_STATUS[s].label}</dt>
                      <dd className="text-lg font-semibold tabular-nums">{num(dry.data.summary[s])}</dd>
                    </div>
                  ))}
                </dl>
                {(dry.data.summary.invalid_email > 0 || dry.data.summary.closed_stage > 0 || dry.data.summary.not_found > 0) && (
                  <p className="text-xs text-muted-foreground">
                    Also skipped: {num(dry.data.summary.invalid_email)} invalid email, {num(dry.data.summary.closed_stage)} closed stage
                    {dry.data.summary.not_found ? `, ${num(dry.data.summary.not_found)} not found` : ""}.
                  </p>
                )}
                <div className="flex items-center justify-between gap-2">
                  <NativeSelect aria-label="Show" value={filter} onChange={(e) => setFilter(e.target.value as "all" | "attention")}>
                    <option value="all">All prospects</option>
                    <option value="attention">Only skipped / needs attention</option>
                  </NativeSelect>
                  <span className="text-xs text-muted-foreground">Preview shows the exact personalized emails.</span>
                </div>
                <div className="max-h-80 overflow-y-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Prospect</TableHead>
                        <TableHead>Result</TableHead>
                        <TableHead className="w-24">
                          <span className="sr-only">Preview</span>
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {shown.map((l) => (
                        <TableRow key={l.contact_id}>
                          <TableCell className="max-w-64">
                            <div className="truncate font-medium">{fullName({ ...l, email: l.email ?? undefined })}</div>
                            <div className="truncate text-xs text-muted-foreground">
                              {[l.company_name, l.email].filter(Boolean).join(" · ")}
                            </div>
                          </TableCell>
                          <TableCell>
                            <EnrollBadge status={l.status} />
                            {l.missing.length > 0 && (
                              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{l.missing.map((m) => `{{${m}}}`).join(" ")}</div>
                            )}
                          </TableCell>
                          <TableCell>
                            {l.email && (
                              <Button variant="ghost" size="sm" onClick={() => setPreviewing(l.contact_id)}>
                                <Eye /> Preview
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {dry.data.summary.missing_fields > 0 && (
                    <span className="mr-auto text-xs text-muted-foreground">
                      Prospects missing merge fields are skipped. Fill the data, or use fallbacks in the campaign’s settings.
                    </span>
                  )}
                  <Button variant="outline" onClick={() => onOpenChange(false)}>
                    Cancel
                  </Button>
                  <Button disabled={!ready || enroll.isPending} onClick={() => enroll.mutate()}>
                    Add {num(ready)} ready prospect{ready === 1 ? "" : "s"}
                  </Button>
                </div>
              </>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
