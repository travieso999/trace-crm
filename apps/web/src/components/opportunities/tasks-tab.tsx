"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlarmClock, Bell, Check, ListTodo, Pencil, Plus, RotateCcw, Settings2, Trash2 } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, NativeSelect, Spinner } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { COMMON_TIMEZONES } from "@/lib/format";
import type { OppTask } from "@/lib/types";
import { REMINDER_OPTIONS, dueLabel, reminderLabel, useDirty, useInvalidateOpportunity } from "./shared";

type TaskTypes = { task_types: string[]; default_timezone: string };
type Draft = { title: string; description: string; task_type: string; due_date: string; due_time: string; timezone: string; reminder: string };

const emptyDraft = (types: TaskTypes): Draft => ({
  title: "", description: "", task_type: types.task_types.includes("Follow-up") ? "Follow-up" : types.task_types[0],
  due_date: "", due_time: "", timezone: types.default_timezone, reminder: "",
});

const toDraft = (t: OppTask, types: TaskTypes): Draft => ({
  title: t.title, description: t.description ?? "", task_type: t.task_type, due_date: t.due_date ?? "", due_time: t.due_time ?? "",
  timezone: t.timezone ?? types.default_timezone, reminder: t.reminder_minutes === null ? "" : String(t.reminder_minutes),
});

const toBody = (d: Draft) => ({
  title: d.title.trim(), description: d.description.trim() || null, task_type: d.task_type,
  due_date: d.due_date || null, due_time: d.due_date ? d.due_time || null : null, timezone: d.timezone,
  reminder_minutes: d.reminder === "" ? null : Number(d.reminder),
});

export function useTaskTypes() {
  return useQuery({ queryKey: ["task-types"], queryFn: () => api<TaskTypes>("/api/opportunities/task-types"), staleTime: 300_000 });
}

function TaskForm({ types, initial, submitLabel, pending, onSubmit, onCancel, dirtyKey }: {
  types: TaskTypes;
  initial: Draft;
  submitLabel: string;
  pending: boolean;
  onSubmit: (d: Draft) => void;
  onCancel: () => void;
  dirtyKey: string;
}) {
  const [d, setD] = React.useState(initial);
  useDirty(dirtyKey, JSON.stringify(d) !== JSON.stringify(initial));
  const zones = COMMON_TIMEZONES.includes(d.timezone) ? COMMON_TIMEZONES : [d.timezone, ...COMMON_TIMEZONES];
  const id = (s: string) => `${dirtyKey}-${s}`;
  return (
    <form
      className="grid gap-3 rounded-xl border bg-muted/20 p-3 animate-page-in"
      onSubmit={(e) => {
        e.preventDefault();
        if (d.title.trim()) onSubmit(d);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation(); // close the form, not the whole panel
          onCancel();
        }
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_11rem]">
        <Field label="Title" htmlFor={id("title")}>
          <Input id={id("title")} autoFocus value={d.title} placeholder="Call to discuss the bond" onChange={(e) => setD({ ...d, title: e.target.value })} />
        </Field>
        <Field label="Type" htmlFor={id("type")}>
          <NativeSelect id={id("type")} className="w-full" value={d.task_type} onChange={(e) => setD({ ...d, task_type: e.target.value })}>
            {[...new Set([...types.task_types, d.task_type])].map((t) => <option key={t} value={t}>{t}</option>)}
          </NativeSelect>
        </Field>
      </div>
      <Field label="Description (optional)" htmlFor={id("desc")}>
        <Textarea id={id("desc")} rows={2} value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Due date" htmlFor={id("date")}>
          <Input id={id("date")} type="date" value={d.due_date} onChange={(e) => setD({ ...d, due_date: e.target.value })} />
        </Field>
        <Field label="Time" htmlFor={id("time")} hint={d.due_date && !d.due_time ? "Defaults to 9:00 AM" : undefined}>
          <Input id={id("time")} type="time" value={d.due_time} disabled={!d.due_date} onChange={(e) => setD({ ...d, due_time: e.target.value })} />
        </Field>
        <Field label="Timezone" htmlFor={id("tz")}>
          <NativeSelect id={id("tz")} className="w-full" value={d.timezone} onChange={(e) => setD({ ...d, timezone: e.target.value })}>
            {zones.map((z) => <option key={z} value={z}>{z.replace(/_/g, " ")}</option>)}
          </NativeSelect>
        </Field>
        <Field label="Reminder" htmlFor={id("rem")}>
          <NativeSelect id={id("rem")} className="w-full" value={d.reminder} disabled={!d.due_date} onChange={(e) => setD({ ...d, reminder: e.target.value })}>
            {REMINDER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </NativeSelect>
        </Field>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button type="submit" size="sm" disabled={pending || !d.title.trim()}>{pending && <Spinner className="text-primary-foreground" />} {submitLabel}</Button>
      </div>
    </form>
  );
}

function TaskTypesDialog({ open, onOpenChange, types }: { open: boolean; onOpenChange: (o: boolean) => void; types: TaskTypes }) {
  const qc = useQueryClient();
  const [text, setText] = React.useState(types.task_types.join("\n"));
  const save = useMutation({
    mutationFn: () => api<TaskTypes>("/api/opportunities/task-types", { method: "PUT", body: { task_types: text.split("\n").map((s) => s.trim()).filter(Boolean) } }),
    onSuccess: (r) => {
      qc.setQueryData(["task-types"], r);
      toast.success("Task types saved");
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Task types</DialogTitle>
          <DialogDescription>One per line. Existing tasks keep their type.</DialogDescription>
        </DialogHeader>
        <Textarea aria-label="Task types" rows={9} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function TaskRow({ t, types, canWrite, contactId }: { t: OppTask; types: TaskTypes; canWrite: boolean; contactId: string }) {
  const invalidate = useInvalidateOpportunity(contactId);
  const [editing, setEditing] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const patch = useMutation({
    mutationFn: (body: object) => api<OppTask>(`/api/opportunities/tasks/${t.id}`, { method: "PATCH", body }),
    onSuccess: (_r, body) => {
      if ("completed" in body) toast.success((body as { completed: boolean }).completed ? "Task completed" : "Task reopened");
      else toast.success("Task updated");
      setEditing(false);
      invalidate("opportunity-tasks", "opportunity-activity");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/opportunities/tasks/${t.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Task deleted");
      setDeleting(false);
      invalidate("opportunity-tasks", "opportunity-activity");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (editing)
    return (
      <li>
        <TaskForm types={types} initial={toDraft(t, types)} submitLabel="Save task" pending={patch.isPending} dirtyKey={`task-${t.id}`}
          onSubmit={(d) => patch.mutate(toBody(d))} onCancel={() => setEditing(false)} />
      </li>
    );
  const done = t.status === "completed";
  const overdue = t.status === "overdue";
  return (
    <li className={cn("group flex items-start gap-3 rounded-xl border p-3 transition-colors", overdue && "border-destructive/40 bg-destructive/5", done && "opacity-70")}>
      <button
        type="button"
        disabled={!canWrite || patch.isPending}
        onClick={() => patch.mutate({ completed: !done })}
        aria-label={done ? `Reopen “${t.title}”` : `Complete “${t.title}”`}
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
          done ? "border-primary bg-primary text-primary-foreground" : "hover:border-primary hover:bg-primary/10",
        )}
      >
        {done && <Check className="size-3" />}
      </button>
      <div className="grid min-w-0 flex-1 gap-0.5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className={cn("font-medium", done && "line-through")}>{t.title}</span>
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{t.task_type}</span>
          {overdue && (
            <span className="inline-flex items-center gap-1 rounded-md bg-destructive/15 px-1.5 py-0.5 text-[11px] font-medium text-destructive">
              <AlarmClock className="size-3" aria-hidden /> Overdue
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span className={cn(overdue && "text-destructive")}>{dueLabel(t)}</span>
          {t.reminder_minutes !== null && !done && (
            <span className="inline-flex items-center gap-1" title={t.reminder_sent_at ? "Reminder sent" : "Reminder scheduled"}>
              <Bell className="size-3" aria-hidden /> {reminderLabel(t.reminder_minutes)}{t.reminder_sent_at ? " · sent" : ""}
            </span>
          )}
          {done && t.completed_at && <span>Completed {new Date(t.completed_at).toLocaleString()}</span>}
        </div>
        {t.description && <p className="mt-1 text-sm whitespace-pre-wrap text-muted-foreground">{t.description}</p>}
      </div>
      {canWrite && (
        <div className="flex shrink-0 gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
          {done ? (
            <Button variant="ghost" size="icon-sm" aria-label={`Reopen “${t.title}”`} onClick={() => patch.mutate({ completed: false })}><RotateCcw /></Button>
          ) : (
            <Button variant="ghost" size="icon-sm" aria-label={`Edit “${t.title}”`} onClick={() => setEditing(true)}><Pencil /></Button>
          )}
          <Button variant="ghost" size="icon-sm" aria-label={`Delete “${t.title}”`} onClick={() => setDeleting(true)}><Trash2 /></Button>
        </div>
      )}
      <ConfirmDialog open={deleting} onOpenChange={setDeleting} title="Delete this task?"
        description={`“${t.title}” will be removed. The activity history keeps a record that it existed.`}
        confirmLabel="Delete" destructive pending={remove.isPending} onConfirm={() => remove.mutate()} />
    </li>
  );
}

const FILTERS = [
  ["all", "All"],
  ["upcoming", "Upcoming"],
  ["overdue", "Overdue"],
  ["completed", "Completed"],
] as const;

export function TasksTab({ contactId, canWrite }: { contactId: string; canWrite: boolean }) {
  const invalidate = useInvalidateOpportunity(contactId);
  const types = useTaskTypes();
  const [status, setStatus] = React.useState<(typeof FILTERS)[number][0]>("all");
  const [type, setType] = React.useState("");
  const [sort, setSort] = React.useState("due");
  const [adding, setAdding] = React.useState(false);
  const [editTypes, setEditTypes] = React.useState(false);
  const list = useQuery({
    queryKey: ["opportunity-tasks", contactId, status, type, sort],
    queryFn: () => api<OppTask[]>(`/api/opportunities/${contactId}/tasks`, { query: { status, task_type: type, sort } }),
  });
  const create = useMutation({
    mutationFn: (d: Draft) => api<OppTask>(`/api/opportunities/${contactId}/tasks`, { body: toBody(d) }),
    onSuccess: (t) => {
      toast.success("Task added", { description: t.reminder_minutes !== null ? `Reminder: ${reminderLabel(t.reminder_minutes)}` : undefined });
      setAdding(false);
      invalidate("opportunity-tasks", "opportunity-activity");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const rows = list.data ?? [];
  const groups: [string, OppTask[]][] =
    status === "all"
      ? [
          ["Overdue", rows.filter((t) => t.status === "overdue")],
          ["Upcoming", rows.filter((t) => t.status === "open")],
          ["Completed", rows.filter((t) => t.status === "completed")],
        ]
      : [["", rows]];
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Filter tasks" className="inline-flex rounded-lg bg-muted p-0.5">
          {FILTERS.map(([v, l]) => (
            <button key={v} role="tab" aria-selected={status === v} type="button" onClick={() => setStatus(v)}
              className={cn("rounded-md px-2.5 py-1 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                status === v ? "bg-background text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
              {l}
            </button>
          ))}
        </div>
        <NativeSelect aria-label="Filter by type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All types</option>
          {(types.data?.task_types ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Sort tasks" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="due">Sort: due date</option>
          <option value="created">Sort: newest</option>
          <option value="type">Sort: type</option>
        </NativeSelect>
        <div className="ml-auto flex gap-1">
          {canWrite && types.data && (
            <Button variant="ghost" size="icon-sm" aria-label="Edit task types" title="Edit task types" onClick={() => setEditTypes(true)}><Settings2 /></Button>
          )}
          {canWrite && !adding && <Button size="sm" onClick={() => setAdding(true)} disabled={!types.data}><Plus /> New task</Button>}
        </div>
      </div>
      {adding && types.data && (
        <TaskForm types={types.data} initial={emptyDraft(types.data)} submitLabel="Add task" pending={create.isPending} dirtyKey="task-new"
          onSubmit={(d) => create.mutate(d)} onCancel={() => setAdding(false)} />
      )}
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending || !types.data ? (
        <div className="grid gap-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : rows.length === 0 ? (
        <EmptyState icon={ListTodo} title={status === "all" && !type ? "No tasks yet" : "No matching tasks"}
          description={status === "all" && !type ? "Schedule a call, follow-up or payment reminder. Reminders appear in the bell." : "Try another filter."} />
      ) : (
        groups.filter(([, g]) => g.length).map(([label, g]) => (
          <section key={label || "all"} aria-label={label || undefined} className="grid gap-2">
            {label && <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label} · {g.length}</h3>}
            <ul className="grid gap-2">
              {g.map((t) => <TaskRow key={t.id} t={t} types={types.data!} canWrite={canWrite} contactId={contactId} />)}
            </ul>
          </section>
        ))
      )}
      {types.data && editTypes && <TaskTypesDialog open={editTypes} onOpenChange={setEditTypes} types={types.data} />}
    </div>
  );
}
