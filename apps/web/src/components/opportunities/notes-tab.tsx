"use client";

import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, ArchiveRestore, History, NotebookPen, Pencil } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Spinner } from "@/components/common";
import { api, errorMessage } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Note, NoteRevision } from "@/lib/types";
import { useDirty, useInvalidateOpportunity } from "./shared";

function Composer({ contactId }: { contactId: string }) {
  const invalidate = useInvalidateOpportunity(contactId);
  const [body, setBody] = React.useState("");
  useDirty("note-new", body.trim().length > 0);
  const add = useMutation({
    mutationFn: () => api<Note>(`/api/opportunities/${contactId}/notes`, { body: { body } }),
    onSuccess: () => {
      toast.success("Note added");
      setBody("");
      invalidate("opportunity-notes", "opportunity-activity");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <form
      className="grid gap-2 rounded-xl border bg-muted/20 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (body.trim()) add.mutate();
      }}
    >
      <Textarea aria-label="New note" rows={4} placeholder="What happened, what was agreed, what’s next…" value={body}
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && body.trim()) {
            e.preventDefault();
            add.mutate();
          }
        }} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">Ctrl + Enter to save</span>
        <Button type="submit" size="sm" disabled={add.isPending || !body.trim()}>{add.isPending && <Spinner className="text-primary-foreground" />} Add note</Button>
      </div>
    </form>
  );
}

function RevisionsDialog({ note, onClose }: { note: Note | null; onClose: () => void }) {
  const revs = useQuery({
    queryKey: ["note-revisions", note?.id],
    queryFn: () => api<NoteRevision[]>(`/api/notes/${note!.id}/revisions`),
    enabled: !!note,
  });
  return (
    <Dialog open={!!note} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit history</DialogTitle>
          <DialogDescription>Earlier versions of this note, oldest first. Nothing is overwritten.</DialogDescription>
        </DialogHeader>
        {revs.isPending ? <Spinner /> : (
          <ol className="grid max-h-[60vh] gap-3 overflow-y-auto">
            {(revs.data ?? []).map((r, i) => (
              <li key={i} className="rounded-lg border p-3">
                <p className="text-sm whitespace-pre-wrap">{r.body}</p>
                <p className="mt-2 text-xs text-muted-foreground">Replaced {dateTime(r.replaced_at)}{r.replaced_by ? ` by ${r.replaced_by}` : ""}</p>
              </li>
            ))}
            {note && (
              <li className="rounded-lg border border-primary/40 p-3">
                <p className="text-sm whitespace-pre-wrap">{note.body}</p>
                <p className="mt-2 text-xs text-muted-foreground">Current version</p>
              </li>
            )}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}

function NoteItem({ n, contactId, canWrite, onHistory }: { n: Note; contactId: string; canWrite: boolean; onHistory: () => void }) {
  const invalidate = useInvalidateOpportunity(contactId);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(n.body);
  const [archiving, setArchiving] = React.useState(false);
  useDirty(`note-${n.id}`, editing && draft.trim() !== n.body);
  const edit = useMutation({
    mutationFn: () => api<Note>(`/api/notes/${n.id}`, { method: "PATCH", body: { body: draft } }),
    onSuccess: () => {
      toast.success("Note updated", { description: "The previous version is kept in its history." });
      setEditing(false);
      invalidate("opportunity-notes", "opportunity-activity");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const archive = useMutation({
    mutationFn: (archived: boolean) => api<Note>(`/api/notes/${n.id}/archive`, { method: "POST", query: { archived } }),
    onSuccess: (_r, archived) => {
      toast.success(archived ? "Note archived" : "Note restored");
      setArchiving(false);
      invalidate("opportunity-notes", "opportunity-activity");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <li className={cn("group grid gap-2 rounded-xl border p-3", n.archived_at && "border-dashed opacity-60")}>
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{n.author ?? "Unknown"}</span>
        <time dateTime={n.created_at}>{dateTime(n.created_at)}</time>
        {n.edited_at && <span>· edited {dateTime(n.edited_at)}{n.edited_by ? ` by ${n.edited_by}` : ""}</span>}
        {n.legacy && <span className="rounded bg-muted px-1.5 py-0.5">from prospect timeline</span>}
        {n.archived_at && <span className="rounded bg-muted px-1.5 py-0.5">archived</span>}
        {canWrite && !n.legacy && !editing && (
          <span className="ml-auto flex gap-0.5 opacity-100 transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100">
            {n.revisions > 0 && <Button variant="ghost" size="icon-xs" aria-label="View edit history" title={`${n.revisions} earlier version(s)`} onClick={onHistory}><History /></Button>}
            {!n.archived_at && <Button variant="ghost" size="icon-xs" aria-label="Edit note" onClick={() => { setDraft(n.body); setEditing(true); }}><Pencil /></Button>}
            {n.archived_at ? (
              <Button variant="ghost" size="icon-xs" aria-label="Restore note" onClick={() => archive.mutate(false)}><ArchiveRestore /></Button>
            ) : (
              <Button variant="ghost" size="icon-xs" aria-label="Archive note" onClick={() => setArchiving(true)}><Archive /></Button>
            )}
          </span>
        )}
        {!canWrite && n.revisions > 0 && <Button variant="ghost" size="icon-xs" className="ml-auto" aria-label="View edit history" onClick={onHistory}><History /></Button>}
      </div>
      {editing ? (
        <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) edit.mutate(); }}
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setEditing(false); } }}>
          <Textarea aria-label="Edit note" rows={4} autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
            <Button type="submit" size="sm" disabled={edit.isPending || !draft.trim() || draft.trim() === n.body}>Save</Button>
          </div>
        </form>
      ) : (
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{n.body}</p>
      )}
      <ConfirmDialog open={archiving} onOpenChange={setArchiving} title="Archive this note?"
        description="It’s hidden from the notes list but kept, with its history. You can restore it from “Show archived”."
        confirmLabel="Archive" pending={archive.isPending} onConfirm={() => archive.mutate(true)} />
    </li>
  );
}

export function NotesTab({ contactId, canWrite }: { contactId: string; canWrite: boolean }) {
  const [showArchived, setShowArchived] = React.useState(false);
  const [history, setHistory] = React.useState<Note | null>(null);
  const notes = useQuery({
    queryKey: ["opportunity-notes", contactId, showArchived],
    queryFn: () => api<Note[]>(`/api/opportunities/${contactId}/notes`, { query: { include_archived: showArchived } }),
  });
  return (
    <div className="grid gap-4">
      {canWrite && <Composer contactId={contactId} />}
      <label className="flex items-center gap-2 justify-self-end text-xs text-muted-foreground">
        <Switch checked={showArchived} onCheckedChange={setShowArchived} aria-label="Show archived notes" /> Show archived
      </label>
      {notes.isError ? (
        <ErrorState error={notes.error} onRetry={() => notes.refetch()} />
      ) : notes.isPending ? (
        <div className="grid gap-2">{[0, 1].map((i) => <Skeleton key={i} className="h-20" />)}</div>
      ) : notes.data.length === 0 ? (
        <EmptyState icon={NotebookPen} title="No notes yet" description="Notes are timestamped and keep their edit history." />
      ) : (
        <ol className="grid gap-2">
          {notes.data.map((n) => <NoteItem key={n.id} n={n} contactId={contactId} canWrite={canWrite} onHistory={() => setHistory(n)} />)}
        </ol>
      )}
      <RevisionsDialog note={history} onClose={() => setHistory(null)} />
    </div>
  );
}
