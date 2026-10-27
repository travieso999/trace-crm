"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlarmClock, CalendarClock, StickyNote } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, NativeSelect, Spinner } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { dateTime, relTime } from "@/lib/format";
import type { OppDetail, OppStatus, Stage } from "@/lib/types";
import { dueLabel, useDirty, useInvalidateOpportunity } from "./shared";

type Form = { first_name: string; last_name: string; company_name: string; email: string; phone: string; stage_id: string; status: OppStatus };

const fromDetail = (d: OppDetail): Form => ({
  first_name: d.first_name ?? "", last_name: d.last_name ?? "", company_name: d.company_name ?? "",
  email: d.email, phone: d.phone ?? "", stage_id: d.stage_id, status: d.status,
});

export function OverviewTab({ detail, stages, canWrite, onOpenTab }: {
  detail: OppDetail;
  stages: Stage[];
  canWrite: boolean;
  onOpenTab: (tab: string) => void;
}) {
  const invalidate = useInvalidateOpportunity(detail.id);
  const [form, setForm] = React.useState<Form>(() => fromDetail(detail));
  const [base, setBase] = React.useState(detail.updated_at);
  if (base !== detail.updated_at) {
    // the record changed elsewhere (drag on the board, another tab): take the fresh values
    setBase(detail.updated_at);
    setForm(fromDetail(detail));
  }
  const initial = fromDetail(detail);
  const changed = (Object.keys(form) as (keyof Form)[]).filter((k) => form[k] !== initial[k]);
  useDirty("overview", changed.length > 0);

  const save = useMutation({
    mutationFn: () =>
      api<OppDetail>(`/api/opportunities/${detail.id}`, {
        method: "PATCH",
        body: Object.fromEntries(changed.map((k) => [k, k === "phone" || k === "company_name" ? form[k] || null : form[k]])),
      }),
    onSuccess: (d) => {
      toast.success("Opportunity saved");
      setBase(d.updated_at);
      setForm(fromDetail(d));
      invalidate("opportunity-activity");
    },
    onError: (e) => toast.error("Couldn’t save", { description: errorMessage(e) }),
  });

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  const next = detail.next_task_detail;
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <form
        className="grid content-start gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (changed.length) save.mutate();
        }}
      >
        <section aria-labelledby="ov-contact" className="grid gap-3">
          <h3 id="ov-contact" className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Client &amp; business</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="First name" htmlFor="ov-first"><Input id="ov-first" value={form.first_name} disabled={!canWrite} onChange={set("first_name")} autoComplete="off" /></Field>
            <Field label="Last name" htmlFor="ov-last"><Input id="ov-last" value={form.last_name} disabled={!canWrite} onChange={set("last_name")} autoComplete="off" /></Field>
            <Field label="Business name" htmlFor="ov-company" className="sm:col-span-2"
              hint={detail.company ? "Matches an existing company by name, or creates one." : undefined}>
              <Input id="ov-company" value={form.company_name} disabled={!canWrite} onChange={set("company_name")} autoComplete="off" />
            </Field>
            <Field label="Email" htmlFor="ov-email"><Input id="ov-email" type="email" value={form.email} disabled={!canWrite} onChange={set("email")} autoComplete="off" /></Field>
            <Field label="Phone" htmlFor="ov-phone"><Input id="ov-phone" type="tel" value={form.phone} disabled={!canWrite} onChange={set("phone")} autoComplete="off" /></Field>
          </div>
        </section>
        <section aria-labelledby="ov-deal" className="grid gap-3">
          <h3 id="ov-deal" className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Opportunity</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Stage" htmlFor="ov-stage">
              <NativeSelect id="ov-stage" className="w-full" value={form.stage_id} disabled={!canWrite} onChange={set("stage_id")}>
                {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </NativeSelect>
            </Field>
            <Field label="Status" htmlFor="ov-status">
              <NativeSelect id="ov-status" className="w-full" value={form.status} disabled={!canWrite} onChange={set("status")}>
                <option value="open">Open</option>
                <option value="won">Won</option>
                <option value="lost">Lost</option>
              </NativeSelect>
            </Field>
          </div>
        </section>
        {canWrite && (
          <div
            className={cn(
              "sticky bottom-0 -mx-1 flex items-center justify-end gap-2 rounded-lg px-1 py-2 transition-opacity",
              changed.length ? "bg-popover opacity-100" : "pointer-events-none opacity-0",
            )}
            aria-hidden={!changed.length}
          >
            <span className="mr-auto text-xs text-muted-foreground">{changed.length} unsaved change{changed.length === 1 ? "" : "s"}</span>
            <Button type="button" variant="ghost" size="sm" onClick={() => setForm(initial)} tabIndex={changed.length ? 0 : -1}>Discard</Button>
            <Button type="submit" size="sm" disabled={save.isPending || !changed.length} tabIndex={changed.length ? 0 : -1}>
              {save.isPending && <Spinner className="text-primary-foreground" />} Save changes
            </Button>
          </div>
        )}
      </form>

      <aside className="grid content-start gap-3">
        <button type="button" onClick={() => onOpenTab("tasks")}
          className="group grid gap-1 rounded-xl border bg-muted/30 p-3 text-left outline-none transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring">
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"><CalendarClock className="size-3.5" aria-hidden /> Next task</span>
          {next ? (
            <>
              <span className="font-medium">{next.title}</span>
              <span className={cn("flex items-center gap-1 text-xs", next.overdue ? "text-destructive" : "text-muted-foreground")}>
                {next.overdue && <AlarmClock className="size-3" aria-hidden />}
                {next.overdue ? "Overdue · " : ""}{next.task_type} · {dueLabel(next)}
              </span>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">Nothing scheduled. Add a task.</span>
          )}
        </button>
        <button type="button" onClick={() => onOpenTab("notes")}
          className="grid gap-1 rounded-xl border bg-muted/30 p-3 text-left outline-none transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring">
          <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground"><StickyNote className="size-3.5" aria-hidden /> Latest note</span>
          {detail.latest_note ? (
            <>
              <span className="line-clamp-4 text-sm whitespace-pre-wrap">{detail.latest_note.body}</span>
              <time className="text-xs text-muted-foreground" dateTime={detail.latest_note.created_at} title={dateTime(detail.latest_note.created_at)}>
                {relTime(detail.latest_note.created_at)}
              </time>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">No notes yet.</span>
          )}
        </button>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-xl border p-3 text-xs">
          <dt className="text-muted-foreground">Open tasks</dt><dd className="text-right tabular-nums">{detail.open_tasks}</dd>
          <dt className="text-muted-foreground">Location</dt><dd className="truncate text-right">{[detail.city, detail.state].filter(Boolean).join(", ") || "—"}</dd>
          <dt className="text-muted-foreground">Prospect since</dt><dd className="text-right">{dateTime(detail.created_at)}</dd>
        </dl>
      </aside>
    </div>
  );
}
