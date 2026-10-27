"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, ExternalLink, Pencil, Trash2, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, StatusBadge } from "@/components/common";
import { CompanyForm, companyPayload, type CompanyValues } from "@/components/company-form";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { dateTime, fullName } from "@/lib/format";
import type { Company, LeadStatus } from "@/lib/types";

type CompanyContact = { id: string; email: string; first_name: string | null; last_name: string | null; job_title: string | null; lead_status: LeadStatus };

export default function CompanyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const [editing, setEditing] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const company = useQuery({ queryKey: ["company", id], queryFn: () => api<Company>(`/api/companies/${id}`) });
  const contacts = useQuery({ queryKey: ["company-contacts", id], queryFn: () => api<CompanyContact[]>(`/api/companies/${id}/contacts`) });

  const remove = useMutation({
    mutationFn: () => api(`/api/companies/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Company deleted");
      qc.invalidateQueries({ queryKey: ["companies"] });
      router.replace("/companies");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  async function save(v: CompanyValues) {
    try {
      const updated = await api<Company>(`/api/companies/${id}`, { method: "PATCH", body: companyPayload(v) });
      qc.setQueryData(["company", id], updated);
      qc.invalidateQueries({ queryKey: ["companies"] });
      toast.success("Saved");
      setEditing(false);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  if (company.isError) return <ErrorState error={company.error} onRetry={() => company.refetch()} />;
  if (company.isPending) return <Skeleton className="h-96" />;
  const c = company.data;
  const href = c.website ? (/^https?:\/\//i.test(c.website) ? c.website : `https://${c.website}`) : null;

  return (
    <>
      <Link href="/companies" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Companies
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight">{c.name}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {href && (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1 hover:underline">
                {c.domain ?? c.website} <ExternalLink className="size-3" aria-hidden />
              </a>
            )}
            <StatusBadge status={c.website_status} />
          </p>
        </div>
        {canWrite && (
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setEditing(true)}>
              <Pencil /> Edit
            </Button>
            <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
              <Trash2 /> Delete
            </Button>
          </div>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Card size="sm" className="content-start">
          <CardContent className="grid gap-2 text-sm">
            {(
              [
                ["Industry", c.industry],
                ["Location", [c.city, c.state].filter(Boolean).join(", ")],
                ["Time zone", c.timezone],
                ["Latest audit", c.latest_audit ? (c.latest_audit.summary ?? c.latest_audit.status) : null],
                ["Created", dateTime(c.created_at)],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="flex justify-between gap-3">
                <span className="text-muted-foreground">{label}</span>
                <span className="min-w-0 truncate text-right">{value || "—"}</span>
              </div>
            ))}
            {c.notes && <p className="mt-2 whitespace-pre-wrap border-t pt-2 text-muted-foreground">{c.notes}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Leads at {c.name}</CardTitle>
          </CardHeader>
          <CardContent>
            {contacts.isPending ? (
              <Skeleton className="h-24" />
            ) : contacts.isError ? (
              <ErrorState error={contacts.error} onRetry={() => contacts.refetch()} />
            ) : contacts.data.length === 0 ? (
              <EmptyState icon={Users} title="No leads linked to this company" />
            ) : (
              <ul className="divide-y text-sm">
                {contacts.data.map((p) => (
                  <li key={p.id} className="flex items-center gap-3 py-2">
                    <div className="min-w-0 flex-1">
                      <Link href={`/leads/${p.id}`} className="font-medium hover:underline">
                        {fullName(p)}
                      </Link>
                      <div className="truncate text-xs text-muted-foreground">{[p.job_title, p.email].filter(Boolean).join(" · ")}</div>
                    </div>
                    <StatusBadge status={p.lead_status} />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Edit company</DialogTitle>
          </DialogHeader>
          <CompanyForm company={c} submitLabel="Save changes" onSubmit={save} onCancel={() => setEditing(false)} />
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${c.name}?`}
        description="Its leads are kept and unlinked from the company."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </>
  );
}
