"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, ListOrdered, Pencil, Plus, Trash2, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmailEditor, type EmailEditorHandle } from "@/components/email-editor";
import { EmailFrame } from "@/components/email-frame";
import { VariableButtons } from "@/components/campaign/sequence-editor";
import { AttachmentChip, VideoSection, WarningList } from "@/components/campaign/video-section";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, PageHeader, Spinner, StatusBadge } from "@/components/common";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { num, relTime } from "@/lib/format";
import type { Campaign, PreviewResult, Template, VideoFields } from "@/lib/types";

function SequenceSummary({ c }: { c: Campaign }) {
  const dayOf = c.steps.map((_, i) => c.steps.slice(1, i + 1).reduce((sum, s) => sum + s.delay_days, 0));
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="truncate text-base">
            <Link href={`/campaigns/${c.id}?tab=sequence`} className="hover:underline">
              {c.name}
            </Link>
          </CardTitle>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={c.status} />
            <span>
              {num(c.stats.leads)} prospects · {num(c.stats.sent)} sent
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ol className="grid gap-2 border-l pl-4">
          {c.steps.map((s, i) => {
            const day = dayOf[i];
            return (
              <li key={s.id ?? i} className="relative text-sm">
                <span className="absolute -left-[21px] top-1.5 size-2 rounded-full bg-primary ring-4 ring-card" aria-hidden />
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium">
                    {i === 0 ? s.subject || "No subject yet" : s.reply_in_thread ? "Follow-up in thread" : s.subject || "New thread"}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {i === 0 ? "Day 0" : `+${s.delay_days}${s.business_days ? " biz" : ""} → day ${day}`}
                  </span>
                </div>
                <p className="line-clamp-2 text-xs text-muted-foreground">{s.body || "Empty body"}</p>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

type Draft = VideoFields & { id?: string; name: string; subject: string; body: string };

/** The same check as a campaign email: what this template looks like with a sample prospect. */
function TemplatePreview({ draft }: { draft: Draft }) {
  const { subject, body, video_asset_id, video_preview, video_attach, video_thumbnail_id, video_cta_text } = draft;
  const fields = { subject, body, video_asset_id: video_asset_id ?? null, video_preview: !!video_preview, video_attach: !!video_attach, video_thumbnail_id: video_thumbnail_id ?? null, video_cta_text: video_cta_text ?? null };
  const preview = useQuery({
    queryKey: ["template-preview", fields],
    queryFn: () => api<PreviewResult>("/api/campaigns/preview", { body: fields }),
    enabled: !!body.trim(),
  });
  return (
    <div className="grid content-start gap-2 text-sm">
      <span className="font-medium">Preview</span>
      {!body.trim() ? (
        <p className="text-muted-foreground">Write a body to see a preview.</p>
      ) : preview.isPending ? (
        <Spinner />
      ) : preview.isError ? (
        <p className="text-destructive">{errorMessage(preview.error)}</p>
      ) : !preview.data.ok ? (
        <p className="rounded-md bg-destructive/10 px-2 py-1.5 text-destructive" role="status">
          Missing values for the sample prospect: {preview.data.missing.map((m) => `{{${m}}}`).join(", ")}.
        </p>
      ) : (
        <>
          <div className="font-medium">{preview.data.subject}</div>
          <EmailFrame key={preview.data.html} html={preview.data.html!} title="Template preview" autoHeight />
          {preview.data.preview_link_missing && (
            <p className="rounded-md bg-amber-500/12 px-2 py-1.5 text-xs text-amber-800 dark:text-amber-300" role="status">
              The clickable preview needs a video link before this email can be sent.
            </p>
          )}
          {preview.data.attachment && <AttachmentChip attachment={preview.data.attachment} />}
          <WarningList warnings={preview.data.attachment_warnings ?? []} />
        </>
      )}
    </div>
  );
}

function Templates({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["templates"], queryFn: () => api<Template[]>("/api/templates") });
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [deleting, setDeleting] = React.useState<Template | null>(null);
  const bodyRef = React.useRef<EmailEditorHandle>(null);
  const vars = useQuery({ queryKey: ["template-variables"], queryFn: () => api<{ standard: string[]; custom: string[] }>("/api/campaigns/variables") });
  const save = useMutation({
    mutationFn: (d: Draft) =>
      d.id ? api<Template>(`/api/templates/${d.id}`, { method: "PUT", body: d }) : api<Template>("/api/templates", { body: d }),
    onSuccess: () => {
      toast.success("Template saved");
      qc.invalidateQueries({ queryKey: ["templates"] });
      setDraft(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: (id: string) => api(`/api/templates/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      setDeleting(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <section aria-labelledby="templates-h" className="grid gap-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 id="templates-h" className="text-base font-semibold">
            Templates
          </h2>
          <p className="text-sm text-muted-foreground">Reusable emails, with their video, thumbnail and button, that you can drop into any step.</p>
        </div>
        {canWrite && (
          <Button variant="outline" onClick={() => setDraft({ name: "", subject: "", body: "" })}>
            <Plus /> New template
          </Button>
        )}
      </div>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-24" />
      ) : list.data.length === 0 ? (
        <EmptyState icon={FileText} title="No templates yet" description="Save copy that works so you can reuse it in new campaigns." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {list.data.map((t) => (
            <Card key={t.id} size="sm">
              <CardHeader className="flex flex-row items-start justify-between gap-2">
                <div className="min-w-0">
                  <CardTitle className="truncate text-sm">{t.name}</CardTitle>
                  <div className="truncate text-xs text-muted-foreground">{t.subject || "No subject"}</div>
                </div>
                {canWrite && (
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" size="icon-sm" aria-label={`Edit ${t.name}`} onClick={() => setDraft(t)}>
                      <Pencil />
                    </Button>
                    <Button variant="ghost" size="icon-sm" aria-label={`Delete ${t.name}`} onClick={() => setDeleting(t)}>
                      <Trash2 />
                    </Button>
                  </div>
                )}
              </CardHeader>
              <CardContent>
                <p className="line-clamp-3 whitespace-pre-wrap text-xs text-muted-foreground">{t.body}</p>
                <p className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
                  {t.video_asset_id && <span className="flex items-center gap-1"><Video className="size-3" aria-hidden /> Includes a video ·</span>}
                  Created {relTime(t.created_at)}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>{draft?.id ? "Edit template" : "New template"}</DialogTitle>
          </DialogHeader>
          {draft && (
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
            <div className="grid content-start gap-3">
              <Field label="Name" htmlFor="tpl-name">
                <Input id="tpl-name" maxLength={200} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </Field>
              <Field label="Subject" htmlFor="tpl-subject">
                <Input id="tpl-subject" maxLength={500} value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
              </Field>
              <Field label="Body" htmlFor="tpl-body" hint="Use {{first_name}}, {{company_name}} and other prospect fields.">
                <EmailEditor id="tpl-body" editorRef={bodyRef} signatureButton videoButton={!!draft.video_asset_id && !!draft.video_preview} value={draft.body} onChange={(body) => setDraft({ ...draft, body })} />
              </Field>
              <VideoSection step={draft} index={0} readOnly={false} onChange={setDraft} />
              <VariableButtons names={[...(vars.data?.standard ?? []), ...(vars.data?.custom ?? [])]} onInsert={(n) => bodyRef.current?.insertText(`{{${n}}}`)} />
              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => setDraft(null)}>
                  Cancel
                </Button>
                <Button type="button" disabled={save.isPending || !draft.name.trim()} onClick={() => save.mutate(draft)}>
                  Save template
                </Button>
              </div>
            </div>
            <TemplatePreview draft={draft} />
            </div>
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name}”?`}
        description="Campaigns that already used it keep their copy."
        confirmLabel="Delete"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </section>
  );
}

export default function SequencesPage() {
  const canWrite = useCanWrite();
  const campaigns = useQuery({ queryKey: ["campaigns", "", false], queryFn: () => api<Campaign[]>("/api/campaigns") });
  return (
    <>
      <PageHeader title="Sequences" description="Every campaign’s steps at a glance. Edit a sequence inside its campaign." />
      <div className="grid gap-8">
        {campaigns.isError ? (
          <ErrorState error={campaigns.error} onRetry={() => campaigns.refetch()} />
        ) : campaigns.isPending ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-48" />
            <Skeleton className="h-48" />
          </div>
        ) : campaigns.data.length === 0 ? (
          <EmptyState
            icon={ListOrdered}
            title="No sequences yet"
            description="Sequences live inside campaigns."
            action={
              canWrite && (
                <Link href="/campaigns" className="text-sm font-medium text-signal hover:underline">
                  Create a campaign
                </Link>
              )
            }
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {campaigns.data.map((c) => (
              <SequenceSummary key={c.id} c={c} />
            ))}
          </div>
        )}
        <Templates canWrite={canWrite} />
      </div>
    </>
  );
}
