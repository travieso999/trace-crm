"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, ListOrdered, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, PageHeader, StatusBadge } from "@/components/common";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { num, relTime } from "@/lib/format";
import type { Campaign, Template } from "@/lib/types";

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

type Draft = { id?: string; name: string; subject: string; body: string };

function Templates({ canWrite }: { canWrite: boolean }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["templates"], queryFn: () => api<Template[]>("/api/templates") });
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [deleting, setDeleting] = React.useState<Template | null>(null);
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
          <p className="text-sm text-muted-foreground">Reusable copy you can drop into any step.</p>
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
                <p className="mt-2 text-[11px] text-muted-foreground">Created {relTime(t.created_at)}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{draft?.id ? "Edit template" : "New template"}</DialogTitle>
          </DialogHeader>
          {draft && (
            <form
              className="grid gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate(draft);
              }}
            >
              <Field label="Name" htmlFor="tpl-name">
                <Input id="tpl-name" maxLength={200} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </Field>
              <Field label="Subject" htmlFor="tpl-subject">
                <Input id="tpl-subject" maxLength={500} value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
              </Field>
              <Field label="Body" htmlFor="tpl-body" hint="Use {{first_name}}, {{company_name}} and other prospect fields.">
                <Textarea id="tpl-body" rows={8} className="font-mono text-[13px]" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
              </Field>
              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => setDraft(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={save.isPending || !draft.name.trim()}>
                  Save template
                </Button>
              </div>
            </form>
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
