"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Eye, Plus, Send, Trash2, Users, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConfirmDialog, ErrorState, Field, NativeSelect, Spinner } from "@/components/common";
import { stepLabel } from "@/components/campaign/labels";
import { AttachmentChip, VideoSection, WarningList } from "@/components/campaign/video-section";
import { useMe } from "@/components/app-shell";
import { EmailFrame } from "@/components/email-frame";
import { api, errorMessage } from "@/lib/api";
import { dateTime, fullName, humanize, num, relTime } from "@/lib/format";
import type { Campaign, EmailAccount, PreviewResult, Step, StepAudience, Template } from "@/lib/types";

const blankFollowUp = (): Step => ({ delay_days: 3, business_days: true, subject: "", body: "", reply_in_thread: true, name: null, status: "draft", send_at: null });

/** ISO ⇄ the value a datetime-local input wants (local time, no zone). */
const toLocalInput = (iso: string | null | undefined) =>
  iso ? new Date(new Date(iso).getTime() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : "";
const fromLocalInput = (v: string) => (v ? new Date(v).toISOString() : null);

/** Who a saved email goes to next, who already got it, who won't and why; plus "send now" to everyone waiting. */
function StepAudienceDialog({ campaign, step, position, canWrite, onOpenChange }: {
  campaign: Campaign; step: Step | null; position: number; canWrite: boolean; onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = React.useState(false);
  const q = useQuery({
    queryKey: ["step-audience", campaign.id, step?.id],
    queryFn: () => api<StepAudience>(`/api/campaigns/${campaign.id}/steps/${step!.id}/audience`),
    enabled: !!step?.id,
  });
  const now = useMutation({
    mutationFn: () => api<{ queued: number; message: string }>(`/api/campaigns/${campaign.id}/steps/${step!.id}/send-now`, { method: "POST" }),
    onSuccess: (r) => {
      toast.success(r.message);
      for (const k of ["step-audience", "campaign", "campaign-leads", "steps-report"]) qc.invalidateQueries({ queryKey: [k] });
      setConfirm(false);
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const label = step ? stepLabel(position, step.name) : "";
  const excluded = Object.entries(q.data?.excluded ?? {}).filter(([, n]) => n > 0);
  const canSendNow = canWrite && campaign.status === "active" && step?.status !== "draft" && step?.status !== "paused" && (q.data?.waiting ?? 0) > 0;
  return (
    <Dialog open={!!step} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Recipients · {label}</DialogTitle>
          <DialogDescription>
            {position === 1
              ? "Everyone in the campaign who hasn’t been emailed yet."
              : "Everyone who got the previous email and hasn’t replied, unsubscribed, bounced or been stopped."}
          </DialogDescription>
        </DialogHeader>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : (
          <div className="grid gap-3 text-sm">
            <dl className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border p-2">
                <dt className="text-xs text-muted-foreground">Will receive it</dt>
                <dd className="text-lg font-semibold tabular-nums">{num(q.data.waiting)}</dd>
              </div>
              <div className="rounded-lg border p-2">
                <dt className="text-xs text-muted-foreground">Already received it</dt>
                <dd className="text-lg font-semibold tabular-nums">{num(q.data.already_sent)}</dd>
              </div>
            </dl>
            {excluded.length > 0 && (
              <p className="text-muted-foreground">
                Not sent to: {excluded.map(([k, n]) => `${num(n)} ${humanize(k).toLowerCase()}`).join(" · ")}
              </p>
            )}
            {q.data.sample.length > 0 && (
              <ul className="max-h-56 divide-y overflow-auto rounded-lg border">
                {q.data.sample.map((r) => (
                  <li key={r.contact_id} className="flex items-center justify-between gap-2 px-3 py-1.5">
                    <span className="min-w-0 truncate">
                      {fullName(r)} <span className="text-muted-foreground">· {r.email}</span>
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground" title={dateTime(r.next_send_at)}>
                      {r.next_send_at ? relTime(r.next_send_at) : "when started"}
                    </span>
                  </li>
                ))}
                {q.data.waiting > q.data.sample.length && (
                  <li className="px-3 py-1.5 text-xs text-muted-foreground">and {num(q.data.waiting - q.data.sample.length)} more</li>
                )}
              </ul>
            )}
            {canWrite && step?.status !== "active" && <p className="text-muted-foreground">This email is a {step?.status}: activate it to send.</p>}
            {canWrite && campaign.status !== "active" && <p className="text-muted-foreground">Start or resume the campaign to send.</p>}
            {canSendNow && (
              <div className="flex justify-end">
                <Button onClick={() => setConfirm(true)}>
                  <Zap /> Send now to {num(q.data.waiting)} waiting
                </Button>
              </div>
            )}
          </div>
        )}
        <ConfirmDialog
          open={confirm}
          onOpenChange={setConfirm}
          title={`Send “${label}” now?`}
          description={`It goes to the ${num(q.data?.waiting)} prospect(s) waiting for it, without waiting for its delay or date. Replies, unsubscribes, suppression, the sending window and daily limits still apply, and nobody gets it twice.`}
          confirmLabel="Send now"
          pending={now.isPending}
          onConfirm={() => now.mutate()}
        />
      </DialogContent>
    </Dialog>
  );
}

function VariableButtons({ names, onInsert, disabled }: { names: string[]; onInsert: (v: string) => void; disabled?: boolean }) {
  return (
    <div className="flex flex-wrap gap-1" aria-label="Insert variable">
      {names.map((n) => (
        <button
          key={n}
          type="button"
          disabled={disabled}
          onClick={() => onInsert(n)}
          className="rounded-md border bg-background px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          {`{{${n}}}`}
        </button>
      ))}
    </div>
  );
}

function StepCard({
  step,
  index,
  count,
  readOnly,
  campaignId,
  variables,
  templates,
  onChange,
  onMove,
  onRemove,
  onPreview,
  onRecipients,
}: {
  step: Step;
  index: number;
  count: number;
  readOnly: boolean;
  campaignId: string;
  variables: string[];
  templates: Template[];
  onChange: (s: Step) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  onPreview: () => void;
  onRecipients?: () => void;
}) {
  const bodyRef = React.useRef<HTMLTextAreaElement>(null);
  const first = index === 0;
  const showSubject = first || !step.reply_in_thread;
  function insert(name: string) {
    const el = bodyRef.current;
    const token = `{{${name}}}`;
    if (!el) return onChange({ ...step, body: step.body + token });
    const start = el.selectionStart ?? step.body.length;
    const end = el.selectionEnd ?? start;
    onChange({ ...step, body: step.body.slice(0, start) + token + step.body.slice(end) });
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + token.length, start + token.length);
    });
  }
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="text-sm">
          {stepLabel(index + 1, step.name)}
          {step.status && step.status !== "active" && (
            <span className="ml-2 rounded-md bg-muted px-1.5 py-0.5 text-xs font-normal capitalize text-muted-foreground">{step.status}</span>
          )}
          <span className="ml-2 font-normal text-muted-foreground">
            {first
              ? "Sent when the campaign starts (or when a prospect is added later)"
              : `${step.delay_days} ${step.business_days ? "business " : ""}day${step.delay_days === 1 ? "" : "s"} after the previous email${step.send_at ? `, not before ${dateTime(step.send_at)}` : ""}`}
          </span>
        </CardTitle>
        <div className="flex items-center gap-1">
          {onRecipients && (
            <Button type="button" variant="ghost" size="sm" onClick={onRecipients}>
              <Users /> Recipients
            </Button>
          )}
          <Button type="button" variant="ghost" size="icon-sm" onClick={onPreview} aria-label={`Preview step ${index + 1}`}>
            <Eye />
          </Button>
          {!readOnly && (
            <>
              <Button type="button" variant="ghost" size="icon-sm" disabled={index <= 1} onClick={() => onMove(-1)} aria-label="Move up">
                <ArrowUp />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" disabled={first || index === count - 1} onClick={() => onMove(1)} aria-label="Move down">
                <ArrowDown />
              </Button>
              <Button type="button" variant="ghost" size="icon-sm" disabled={first} onClick={onRemove} aria-label={`Delete step ${index + 1}`}>
                <Trash2 />
              </Button>
            </>
          )}
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
          <Field label="Name (only your team sees it)" htmlFor={`name-${index}`}>
            <Input id={`name-${index}`} maxLength={120} disabled={readOnly} value={step.name ?? ""} placeholder={stepLabel(index + 1)}
              onChange={(e) => onChange({ ...step, name: e.target.value })} />
          </Field>
          <Field label="Status" htmlFor={`status-${index}`}>
            <NativeSelect id={`status-${index}`} className="w-full" disabled={readOnly} value={step.status ?? "active"}
              onChange={(e) => onChange({ ...step, status: e.target.value as Step["status"] })}>
              <option value="active">Active</option>
              <option value="draft">Draft (not sent yet)</option>
              <option value="paused">Paused</option>
            </NativeSelect>
          </Field>
        </div>
        {step.status && step.status !== "active" && (
          <p className="rounded-md bg-muted px-2 py-1.5 text-xs text-muted-foreground">
            Prospects who reach this email wait here until you set it to Active. {first ? "" : "Nobody skips it."}
          </p>
        )}
        {!first && (
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Wait (days)" htmlFor={`delay-${index}`} className="w-28">
              <Input id={`delay-${index}`} type="number" min={0} max={60} disabled={readOnly} value={step.delay_days}
                onChange={(e) => onChange({ ...step, delay_days: Math.max(0, Math.min(60, Number(e.target.value))) })} />
            </Field>
            <label className="flex h-8 items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-[var(--primary)]" disabled={readOnly} checked={step.business_days}
                onChange={(e) => onChange({ ...step, business_days: e.target.checked })} />
              Business days only
            </label>
            <label className="flex h-8 items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-[var(--primary)]" disabled={readOnly} checked={step.reply_in_thread}
                onChange={(e) => onChange({ ...step, reply_in_thread: e.target.checked })} />
              Reply in the same thread
            </label>
            <Field label="Not before (optional)" htmlFor={`sendat-${index}`} className="w-56">
              <Input id={`sendat-${index}`} type="datetime-local" disabled={readOnly} value={toLocalInput(step.send_at)}
                onChange={(e) => onChange({ ...step, send_at: fromLocalInput(e.target.value) })} />
            </Field>
          </div>
        )}
        {showSubject ? (
          <Field label="Subject" htmlFor={`subject-${index}`}>
            <Input id={`subject-${index}`} maxLength={500} disabled={readOnly} value={step.subject}
              onChange={(e) => onChange({ ...step, subject: e.target.value })} placeholder="Quick question for {{company_name}}" />
          </Field>
        ) : (
          <p className="text-xs text-muted-foreground">Subject: “Re: ” + the step 1 subject, so it lands in the same thread.</p>
        )}
        <Field label="Body" htmlFor={`body-${index}`} hint="Plain text. Links are clickable. The unsubscribe footer is added automatically.">
          <Textarea ref={bodyRef} id={`body-${index}`} rows={8} maxLength={20000} disabled={readOnly} value={step.body}
            onChange={(e) => onChange({ ...step, body: e.target.value })} className="font-mono text-[13px]" />
        </Field>
        <VideoSection step={step} index={index} readOnly={readOnly} campaignId={campaignId} onChange={onChange} />
        {!readOnly && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <VariableButtons names={variables} onInsert={insert} />
            {templates.length > 0 && (
              <NativeSelect
                aria-label="Use a template"
                value=""
                onChange={(e) => {
                  const t = templates.find((x) => x.id === e.target.value);
                  if (t) onChange({ ...step, subject: showSubject ? t.subject : step.subject, body: t.body });
                }}
              >
                <option value="">Use template…</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </NativeSelect>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TestSendDialog({ step, campaignId, onOpenChange }: { step: Step | null; campaignId: string; onOpenChange: (o: boolean) => void }) {
  const me = useMe();
  const boxes = useQuery({ queryKey: ["email-accounts"], queryFn: () => api<EmailAccount[]>("/api/email-accounts"), enabled: !!step });
  const connected = (boxes.data ?? []).filter((b) => b.status === "connected");
  const [to, setTo] = React.useState("");
  const [box, setBox] = React.useState("");
  const send = useMutation({
    mutationFn: () =>
      api<{ message: string }>("/api/campaigns/test-send", {
        body: { subject: step!.subject, body: step!.body, campaign_id: campaignId, to_email: to || me.data?.user.email, email_account_id: box || connected[0]?.id, video_asset_id: step!.video_asset_id ?? null, video_preview: !!step!.video_preview, video_attach: !!step!.video_attach },
      }),
    onSuccess: (r) => {
      toast.success(r.message);
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!step} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Send a test</DialogTitle>
          <DialogDescription>Rendered with the first enrolled prospect’s data (or a sample prospect). Only addresses in ALLOWED_TEST_RECIPIENTS can receive tests (sandbox mailboxes also accept app users).</DialogDescription>
        </DialogHeader>
        {step?.video_attach && (
          <p className="rounded-md bg-muted px-2 py-1.5 text-xs text-muted-foreground">
            This test includes the real MP4 and can take a minute. When it arrives, check the attachment shows with the right name and size, then download it and play it. Do this before using the video in a campaign.
          </p>
        )}
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate();
          }}
        >
          <Field label="From mailbox" htmlFor="ts-box">
            <NativeSelect id="ts-box" value={box || connected[0]?.id || ""} onChange={(e) => setBox(e.target.value)}>
              {connected.length === 0 && <option value="">No connected mailbox</option>}
              {connected.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.email}
                  {b.provider === "sandbox" ? " (sandbox)" : ""}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Send to" htmlFor="ts-to">
            <Input id="ts-to" type="email" placeholder={me.data?.user.email} value={to} onChange={(e) => setTo(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={send.isPending || connected.length === 0}>
              <Send /> Send test
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function SequenceEditor({ campaign, canWrite, addSignal = 0 }: { campaign: Campaign; canWrite: boolean; addSignal?: number }) {
  const qc = useQueryClient();
  const readOnly = !canWrite || campaign.status === "completed" || campaign.status === "stopped";
  const [steps, setSteps] = React.useState<Step[]>(campaign.steps);
  const [source, setSource] = React.useState(campaign.steps);
  if (source !== campaign.steps) {
    // server copy changed (saved elsewhere / refetched): adopt it
    setSource(campaign.steps);
    setSteps(campaign.steps);
  }
  const dirty = JSON.stringify(steps) !== JSON.stringify(campaign.steps);
  // the page header's "Add Follow-Up" button bumps addSignal
  const [seenSignal, setSeenSignal] = React.useState(addSignal);
  if (addSignal !== seenSignal) {
    setSeenSignal(addSignal);
    if (!readOnly && steps.length < 20) setSteps((all) => [...all, blankFollowUp()]);
  }
  const [recipientsFor, setRecipientsFor] = React.useState<{ step: Step; position: number } | null>(null);
  const vars = useQuery({ queryKey: ["template-variables"], queryFn: () => api<{ standard: string[]; custom: string[] }>("/api/campaigns/variables") });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<Template[]>("/api/templates") });
  const [previewIdx, setPreviewIdx] = React.useState(0);
  const [testStep, setTestStep] = React.useState<Step | null>(null);

  const previewStep = steps[Math.min(previewIdx, steps.length - 1)];
  const preview = useQuery({
    queryKey: ["preview", campaign.id, previewStep?.subject, previewStep?.body, previewIdx, previewStep?.video_asset_id, previewStep?.video_preview, previewStep?.video_attach],
    queryFn: () =>
      api<PreviewResult>("/api/campaigns/preview", {
        body: { subject: previewIdx > 0 && previewStep.reply_in_thread ? "Re: " + (steps[0]?.subject ?? "") : previewStep.subject, body: previewStep.body, campaign_id: campaign.id, video_asset_id: previewStep.video_asset_id ?? null, video_preview: !!previewStep.video_preview, video_attach: !!previewStep.video_attach },
      }),
    enabled: !!previewStep && !!previewStep.body.trim(),
  });

  const save = useMutation({
    mutationFn: () =>
      api<Campaign>(`/api/campaigns/${campaign.id}/steps`, {
        method: "PUT",
        body: {
          steps: steps.map(({ id, delay_days, business_days, subject, body, reply_in_thread, name, status, send_at, video_asset_id, video_preview, video_attach }) => ({
            id, delay_days, business_days, subject, body, reply_in_thread, name: name?.trim() || null, status: status ?? "active", send_at: send_at ?? null,
            video_asset_id: video_asset_id ?? null, video_preview: !!video_preview, video_attach: !!video_attach,
          })),
        },
      }),
    onSuccess: (c) => {
      qc.setQueryData(["campaign", campaign.id], c);
      qc.invalidateQueries({ queryKey: ["campaign-preflight", campaign.id] });
      toast.success("Sequence saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  function update(i: number, s: Step) {
    setSteps((all) => all.map((x, j) => (j === i ? s : x)));
  }
  function move(i: number, dir: -1 | 1) {
    setSteps((all) => {
      const next = [...all];
      [next[i], next[i + dir]] = [next[i + dir], next[i]];
      return next;
    });
  }
  const variables = [...(vars.data?.standard ?? []), ...(vars.data?.custom ?? [])];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
      <div className="grid content-start gap-3">
        {steps.map((s, i) => (
          <StepCard
            key={s.id ?? `new-${i}`}
            step={s}
            index={i}
            count={steps.length}
            readOnly={readOnly}
            campaignId={campaign.id}
            variables={variables}
            templates={templates.data ?? []}
            onChange={(n) => update(i, n)}
            onMove={(d) => move(i, d)}
            onRemove={() => setSteps((all) => all.filter((_, j) => j !== i))}
            onPreview={() => setPreviewIdx(i)}
            onRecipients={s.id && !dirty ? () => setRecipientsFor({ step: s, position: i + 1 }) : undefined}
          />
        ))}
        {!readOnly && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button type="button" variant="outline" disabled={steps.length >= 20} onClick={() => setSteps((all) => [...all, blankFollowUp()])}>
              <Plus /> Add follow-up
            </Button>
            <div className="flex items-center gap-2">
              {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
              <Button type="button" variant="outline" disabled={!dirty || save.isPending} onClick={() => setSteps(campaign.steps)}>
                Discard
              </Button>
              <Button type="button" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
                Save sequence
              </Button>
            </div>
          </div>
        )}
      </div>
      <Card className="h-fit lg:sticky lg:top-20">
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm">Preview · {stepLabel(Math.min(previewIdx, steps.length - 1) + 1, previewStep?.name)}</CardTitle>
          {canWrite && previewStep && (
            <Button size="sm" variant="outline" disabled={!previewStep.body.trim()} onClick={() => setTestStep(previewStep)}>
              <Send /> Test
            </Button>
          )}
        </CardHeader>
        <CardContent className="grid gap-2 text-sm">
          {!previewStep?.body.trim() ? (
            <p className="text-muted-foreground">Write a body to see a preview.</p>
          ) : preview.isPending ? (
            <Spinner />
          ) : preview.isError ? (
            <p className="text-destructive">{errorMessage(preview.error)}</p>
          ) : !preview.data.ok ? (
            <p className="rounded-md bg-destructive/10 px-2 py-1.5 text-destructive" role="status">
              Missing values for {preview.data.recipient}: {preview.data.missing.map((m) => `{{${m}}}`).join(", ")}. This prospect would be blocked, not sent a broken email.
            </p>
          ) : (
            <>
              <div className="text-xs text-muted-foreground">To {preview.data.recipient}</div>
              <div className="font-medium">{preview.data.subject}</div>
              <EmailFrame key={preview.data.html} html={preview.data.html!} title="Email preview" autoHeight />
              {preview.data.preview_link_missing && (
                <p className="rounded-md bg-amber-500/12 px-2 py-1.5 text-xs text-amber-800 dark:text-amber-300" role="status">
                  The clickable preview needs a video link before this email can be sent.
                </p>
              )}
              {preview.data.attachment && <AttachmentChip attachment={preview.data.attachment} />}
              <WarningList warnings={preview.data.attachment_warnings ?? []} />
            </>
          )}
        </CardContent>
      </Card>
      <TestSendDialog step={testStep} campaignId={campaign.id} onOpenChange={(o) => !o && setTestStep(null)} />
      <StepAudienceDialog campaign={campaign} step={recipientsFor?.step ?? null} position={recipientsFor?.position ?? 1} canWrite={canWrite}
        onOpenChange={(o) => !o && setRecipientsFor(null)} />
    </div>
  );
}
