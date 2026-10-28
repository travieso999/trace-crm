"use client";

/** The last check before a campaign starts: exactly who will be emailed, and who is left out and why. */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorState, Spinner } from "@/components/common";
import { scheduleSummary } from "@/components/schedule-editor";
import { stepLabel } from "@/components/campaign/labels";
import { api } from "@/lib/api";
import { dateTime, num } from "@/lib/format";
import type { Campaign, Preflight, RecipientSummary } from "@/lib/types";

export function SendConfirmDialog({ campaign, open, onOpenChange, onConfirm, pending, onFix }: {
  campaign: Campaign; open: boolean; onOpenChange: (o: boolean) => void; onConfirm: () => void; pending: boolean; onFix: (tab: "sequence" | "contacts" | "settings") => void;
}) {
  const summary = useQuery({
    queryKey: ["campaign-summary", campaign.id],
    queryFn: () => api<RecipientSummary>(`/api/campaigns/${campaign.id}/recipients-summary`),
    enabled: open,
  });
  const pre = useQuery({
    queryKey: ["campaign-preflight", campaign.id],
    queryFn: () => api<Preflight>(`/api/campaigns/${campaign.id}/preflight`),
    enabled: open,
  });
  const [ack, setAck] = React.useState(false);
  const s = summary.data;
  const first = campaign.steps[0];
  const followUps = campaign.steps.slice(1);
  const rows: [string, number, string?][] = s
    ? [
        ["Total campaign contacts", s.total],
        ["Valid email addresses", s.valid_emails, s.invalid_emails ? `${num(s.invalid_emails)} marked invalid` : undefined],
        ["Duplicate addresses", s.duplicate_addresses, "Each address gets each email once"],
        ["Unsubscribed", s.unsubscribed],
        ["Suppressed or bounced", s.suppressed],
        ["Already replied", s.replied],
        ["Paused or finished", s.paused + s.finished_or_stopped],
        [
          "Missing personalization data",
          s.missing_data,
          s.missing_data ? `Empty ${Object.keys(s.missing_variables).map((v) => `{{${v}}}`).join(", ")}: not sent unless you set fallbacks` : undefined,
        ],
      ]
    : [];
  const problems = pre.data?.problems ?? [];
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) setAck(false); onOpenChange(o); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Send “{campaign.name}”?</DialogTitle>
          <DialogDescription>
            {campaign.start_at ? `Starts ${dateTime(campaign.start_at)}` : "Starts now"}, within the sending window ({scheduleSummary(campaign.schedule)}) and daily
            limits, so a large list goes out over several days.
          </DialogDescription>
        </DialogHeader>
        {summary.isPending || pre.isPending ? (
          <Spinner />
        ) : summary.isError ? (
          <ErrorState error={summary.error} />
        ) : (
          <div className="grid gap-3 text-sm">
            <dl className="divide-y rounded-lg border">
              {rows.map(([label, n, hint]) => (
                <div key={label} className="flex items-center justify-between gap-3 px-3 py-1.5">
                  <dt>
                    {label}
                    {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
                  </dt>
                  <dd className="tabular-nums">{num(n)}</dd>
                </div>
              ))}
              <div className="flex items-center justify-between gap-3 bg-muted/50 px-3 py-2 font-semibold">
                <dt>Final eligible recipients</dt>
                <dd className="text-lg tabular-nums">{num(s!.eligible)}</dd>
              </div>
            </dl>
            {first && (
              <div className="rounded-lg border p-3">
                <div className="text-xs text-muted-foreground">{stepLabel(1, first.name)}</div>
                <div className="font-medium">{first.subject || "(no subject)"}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {followUps.length
                    ? `Then ${followUps.length} follow-up${followUps.length === 1 ? "" : "s"} to people who haven’t replied${
                        followUps.some((f) => f.status !== "active") ? " (drafts and paused follow-ups wait until you activate them)" : ""
                      }.`
                    : "No follow-ups yet. You can add them later; they go to the same audience."}
                </div>
              </div>
            )}
            {problems.length > 0 && (
              <ul className="grid gap-1.5 rounded-lg bg-destructive/10 p-3 text-destructive">
                {problems.map((p) => (
                  <li key={p} className="flex items-start gap-2">
                    <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                    <span>
                      {p}{" "}
                      {/step|Step|subject|draft/.test(p) && (
                        <button type="button" className="underline" onClick={() => onFix("sequence")}>Edit emails</button>
                      )}
                      {/email account/.test(p) && (
                        <button type="button" className="underline" onClick={() => onFix("settings")}>Choose mailboxes</button>
                      )}
                      {/Enroll|prospect/.test(p) && (
                        <button type="button" className="underline" onClick={() => onFix("contacts")}>Contacts</button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {s!.missing_data > 0 && (
              <p className="rounded-md bg-amber-500/12 px-3 py-2 text-amber-800 dark:text-amber-300" role="status">
                {num(s!.missing_data)} contact{s!.missing_data === 1 ? " is" : "s are"} missing data the emails use. Fill it in, or use fallback values (e.g. “there” for
                first name){" "}
                <button type="button" className="underline" onClick={() => onFix("settings")}>in Settings</button>.
              </p>
            )}
            <p className="text-xs text-muted-foreground">Tip: send yourself a test from the Email Sequence tab (Preview → Test) first.</p>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              <span>I’ve checked the emails. Send to {num(s!.eligible)} recipient{s!.eligible === 1 ? "" : "s"}.</span>
            </label>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!ack || pending || problems.length > 0 || !s?.eligible} onClick={onConfirm}>
            <Rocket /> {campaign.start_at ? "Schedule campaign" : "Start sending"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
