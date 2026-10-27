"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Eye, Plus, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, NativeSelect, Spinner } from "@/components/common";
import { useMe } from "@/components/app-shell";
import { EmailFrame } from "@/components/email-frame";
import { api, errorMessage } from "@/lib/api";
import type { Campaign, EmailAccount, PreviewResult, Step, Template } from "@/lib/types";

const blankFollowUp = (): Step => ({ delay_days: 2, business_days: true, subject: "", body: "", reply_in_thread: true });

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
  variables,
  templates,
  onChange,
  onMove,
  onRemove,
  onPreview,
}: {
  step: Step;
  index: number;
  count: number;
  readOnly: boolean;
  variables: string[];
  templates: Template[];
  onChange: (s: Step) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  onPreview: () => void;
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
          Step {index + 1}
          <span className="ml-2 font-normal text-muted-foreground">
            {first ? "Sent when the prospect is enrolled" : `${step.delay_days} ${step.business_days ? "business " : ""}day${step.delay_days === 1 ? "" : "s"} after step ${index}`}
          </span>
        </CardTitle>
        <div className="flex items-center gap-1">
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
        body: { subject: step!.subject, body: step!.body, campaign_id: campaignId, to_email: to || me.data?.user.email, email_account_id: box || connected[0]?.id },
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

export function SequenceEditor({ campaign, canWrite }: { campaign: Campaign; canWrite: boolean }) {
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
  const vars = useQuery({ queryKey: ["template-variables"], queryFn: () => api<{ standard: string[]; custom: string[] }>("/api/campaigns/variables") });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<Template[]>("/api/templates") });
  const [previewIdx, setPreviewIdx] = React.useState(0);
  const [testStep, setTestStep] = React.useState<Step | null>(null);

  const previewStep = steps[Math.min(previewIdx, steps.length - 1)];
  const preview = useQuery({
    queryKey: ["preview", campaign.id, previewStep?.subject, previewStep?.body, previewIdx],
    queryFn: () =>
      api<PreviewResult>("/api/campaigns/preview", {
        body: { subject: previewIdx > 0 && previewStep.reply_in_thread ? "Re: " + (steps[0]?.subject ?? "") : previewStep.subject, body: previewStep.body, campaign_id: campaign.id },
      }),
    enabled: !!previewStep && !!previewStep.body.trim(),
  });

  const save = useMutation({
    mutationFn: () =>
      api<Campaign>(`/api/campaigns/${campaign.id}/steps`, {
        method: "PUT",
        body: { steps: steps.map(({ id, delay_days, business_days, subject, body, reply_in_thread }) => ({ id, delay_days, business_days, subject, body, reply_in_thread })) },
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
            variables={variables}
            templates={templates.data ?? []}
            onChange={(n) => update(i, n)}
            onMove={(d) => move(i, d)}
            onRemove={() => setSteps((all) => all.filter((_, j) => j !== i))}
            onPreview={() => setPreviewIdx(i)}
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
          <CardTitle className="text-sm">Preview · step {Math.min(previewIdx, steps.length - 1) + 1}</CardTitle>
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
            </>
          )}
        </CardContent>
      </Card>
      <TestSendDialog step={testStep} campaignId={campaign.id} onOpenChange={(o) => !o && setTestStep(null)} />
    </div>
  );
}
