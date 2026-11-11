"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { History, UserRound } from "lucide-react";
import { Spinner } from "@/components/common";
import { useMe } from "@/components/app-shell";
import { OwnerChip, OwnerSelect } from "@/components/team";
import { api, errorMessage } from "@/lib/api";
import { dateTime, relTime } from "@/lib/format";
import type { ActivityItem, OppDetail, PersonRef } from "@/lib/types";
import { useInvalidateOpportunity } from "./shared";

function When({ at }: { at: string | null }) {
  if (!at) return <span className="text-muted-foreground">—</span>;
  return <time dateTime={at} title={dateTime(at)}>{relTime(at)}</time>;
}

function Who({ p }: { p: PersonRef | null }) {
  return p ? <span className="truncate">{p.name}</span> : <span className="text-muted-foreground">System</span>;
}

/** Assigned To (saved immediately), who created / last changed / reassigned it, and the ownership history. */
export function OwnershipCard({ detail, canAssign }: { detail: OppDetail; canAssign: boolean }) {
  const meId = useMe().data?.user.id;
  const invalidate = useInvalidateOpportunity(detail.id);
  const can = detail.can;
  const mode = !canAssign || !can ? null : can.assign_any ? "any" : can.take || can.release ? "self" : null;
  const assign = useMutation({
    mutationFn: (owner_id: string | null) => api<OppDetail>(`/api/opportunities/${detail.id}`, { method: "PATCH", body: { owner_id } }),
    onSuccess: (d) => {
      toast.success(d.owner ? `Assigned to ${d.owner.name}` : "Now unassigned");
      invalidate("opportunity-activity", "opportunity-ownership");
    },
    onError: (e) => toast.error("Couldn’t change the owner", { description: errorMessage(e) }),
  });
  const history = useQuery({
    queryKey: ["opportunity-ownership", detail.id],
    queryFn: () => api<ActivityItem[]>(`/api/opportunities/${detail.id}/activity`, {
      query: { type: "owner_change", limit: 20 },
    }),
  });
  const a = detail.accountability;
  return (
    <section aria-labelledby="own-title" className="grid gap-3 rounded-xl border p-3">
      <h3 id="own-title" className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <UserRound className="size-3.5" aria-hidden /> Assigned To
      </h3>
      {mode ? (
        <div className="flex items-center gap-2">
          <OwnerSelect
            aria-label="Assigned to"
            className="w-full"
            value={detail.owner?.id ?? null}
            current={detail.owner}
            meId={meId}
            allowed={mode}
            disabled={assign.isPending}
            onChange={(id) => assign.mutate(id)}
          />
          {assign.isPending && <Spinner />}
        </div>
      ) : (
        <OwnerChip owner={detail.owner} className="text-sm" />
      )}
      {mode === "self" && (
        <p className="text-xs text-muted-foreground">You can take unassigned opportunities or hand back your own. An admin can assign to anyone.</p>
      )}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Created by</dt>
        <dd className="flex justify-end gap-1 text-right"><Who p={a.created_by} /><span className="text-muted-foreground">·</span><When at={a.opportunity_created_at} /></dd>
        <dt className="text-muted-foreground">Last updated by</dt>
        <dd className="flex justify-end gap-1 text-right">{a.updated_by ? <><Who p={a.updated_by} /><span className="text-muted-foreground">·</span><When at={a.last_edited_at} /></> : <span className="text-muted-foreground">—</span>}</dd>
        <dt className="text-muted-foreground">Owner changed</dt>
        <dd className="flex justify-end gap-1 text-right">{a.owner_assigned_at ? <><Who p={a.owner_assigned_by} /><span className="text-muted-foreground">·</span><When at={a.owner_assigned_at} /></> : <span className="text-muted-foreground">—</span>}</dd>
      </dl>
      <div className="grid gap-1.5 border-t pt-2.5">
        <h4 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"><History className="size-3.5" aria-hidden /> Ownership history</h4>
        {history.isPending ? (
          <Spinner />
        ) : !history.data?.length ? (
          <p className="text-xs text-muted-foreground">{detail.owner ? `Assigned to ${detail.owner.name} when it was created.` : "Never assigned."}</p>
        ) : (
          <ol className="grid gap-1.5">
            {history.data.map((h) => (
              <li key={h.id} className="text-xs">
                <span>{h.summary}</span>
                <span className="block text-muted-foreground">{h.actor} · <time dateTime={h.at} title={dateTime(h.at)}>{relTime(h.at)}</time></span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
