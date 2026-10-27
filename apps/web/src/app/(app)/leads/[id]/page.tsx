"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, ShieldBan, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, ErrorState, StatusBadge } from "@/components/common";
import { LeadForm, toPayload, type LeadValues } from "@/components/lead-form";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { dateTime, fullName, humanize, relTime } from "@/lib/format";
import type { Activity, Contact } from "@/lib/types";

function Timeline({ contactId, canWrite }: { contactId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const [note, setNote] = React.useState("");
  const acts = useQuery({
    queryKey: ["contact-activities", contactId],
    queryFn: () => api<Activity[]>(`/api/contacts/${contactId}/activities`),
  });
  const add = useMutation({
    mutationFn: (text: string) => api<Activity>(`/api/contacts/${contactId}/notes`, { body: { text } }),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["contact-activities", contactId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Activity</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {canWrite && (
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) add.mutate(note.trim());
            }}
          >
            <Textarea aria-label="Add a note" placeholder="Add a note…" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
            <Button type="submit" size="sm" className="justify-self-end" disabled={add.isPending || !note.trim()}>
              Add note
            </Button>
          </form>
        )}
        {acts.isPending ? (
          <Skeleton className="h-24" />
        ) : acts.isError ? (
          <ErrorState error={acts.error} onRetry={() => acts.refetch()} />
        ) : acts.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No activity yet.</p>
        ) : (
          <ol className="grid gap-3 border-l pl-4">
            {acts.data.map((a) => (
              <li key={a.id} className="relative text-sm">
                <span className="absolute -left-[21px] top-1.5 size-2 rounded-full bg-border ring-4 ring-card" aria-hidden />
                <div className="flex items-baseline gap-2">
                  <span className="font-medium">{humanize(a.type)}</span>
                  <time dateTime={a.created_at} title={dateTime(a.created_at)} className="text-xs text-muted-foreground">
                    {relTime(a.created_at)}
                  </time>
                </div>
                <p className="whitespace-pre-wrap text-muted-foreground">{a.summary}</p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const contact = useQuery({ queryKey: ["contact", id], queryFn: () => api<Contact>(`/api/contacts/${id}`) });

  const remove = useMutation({
    mutationFn: () => api(`/api/contacts/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Lead deleted");
      qc.invalidateQueries({ queryKey: ["contacts"] });
      router.replace("/leads");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  async function save(values: LeadValues) {
    try {
      const updated = await api<Contact>(`/api/contacts/${id}`, { method: "PATCH", body: toPayload(values) });
      qc.setQueryData(["contact", id], updated);
      qc.invalidateQueries({ queryKey: ["contacts"] });
      qc.invalidateQueries({ queryKey: ["contact-activities", id] });
      toast.success("Saved");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  if (contact.isError) return <ErrorState error={contact.error} onRetry={() => contact.refetch()} />;
  if (contact.isPending) return <Skeleton className="h-96" />;
  const c = contact.data;

  return (
    <>
      <Link href="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Leads
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight">{fullName(c)}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>{c.email}</span>
            <StatusBadge status={c.lead_status} />
            {c.suppressed && (
              <span className="inline-flex items-center gap-1 text-destructive">
                <ShieldBan className="size-3.5" aria-hidden /> Suppressed
              </span>
            )}
          </p>
        </div>
        {canWrite && (
          <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
            <Trash2 /> Delete
          </Button>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
        <Card>
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardContent>
            {/* keyed on updated_at so the form resets to the saved server values */}
            <LeadForm key={c.updated_at} contact={c} submitLabel="Save changes" onSubmit={save} disabled={!canWrite} />
          </CardContent>
        </Card>

        <div className="grid content-start gap-4">
          <Card size="sm">
            <CardContent className="grid gap-2 text-sm">
              <Row label="Company">
                {c.company_id ? (
                  <Link href={`/companies/${c.company_id}`} className="hover:underline">
                    {c.company_name ?? "View company"}
                  </Link>
                ) : (
                  (c.company_name ?? "—")
                )}
              </Row>
              <Row label="Time zone">{c.timezone ?? "—"}</Row>
              <Row label="Source">{c.source ?? "—"}</Row>
              <Row label="Last contacted">{relTime(c.last_contacted_at)}</Row>
              <Row label="Last replied">{relTime(c.last_replied_at)}</Row>
              <Row label="Created">{dateTime(c.created_at)}</Row>
            </CardContent>
          </Card>
          {c.campaigns.length > 0 && (
            <Card size="sm">
              <CardHeader>
                <CardTitle>Campaigns</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="grid gap-1.5 text-sm">
                  {c.campaigns.map((cp) => (
                    <li key={cp.id} className="flex items-center justify-between gap-2">
                      <Link href={`/campaigns/${cp.id}`} className="truncate hover:underline">
                        {cp.name}
                      </Link>
                      <StatusBadge status={cp.status} />
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
          <Timeline contactId={c.id} canWrite={canWrite} />
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this lead?"
        description="The lead is removed from all campaigns and any pending sends are cancelled."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate text-right">{children}</span>
    </div>
  );
}
