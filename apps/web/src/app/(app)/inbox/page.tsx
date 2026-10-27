"use client";

import * as React from "react";
import Link from "next/link";
import { cn } from "cn";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Inbox, MailOpen, Search, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog, EmptyState, ErrorState, NativeSelect, PageHeader, Pagination, Spinner, StatusBadge } from "@/components/common";
import { EmailFrame } from "@/components/email-frame";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { dateTime, fullName, humanize, relTime } from "@/lib/format";
import type { EmailAccount, InboxItem, InboxPage, Thread } from "@/lib/types";

const VIEWS = [
  ["all", "All"],
  ["unread", "Unread"],
  ["positive", "Positive"],
  ["neutral", "Neutral"],
  ["negative", "Negative"],
] as const;
const INTENTS = ["positive", "neutral", "negative", "meeting", "not_now", "wrong_person"];
const PAGE_SIZE = 30;

function Row({ item, active, onSelect, checked, onCheck }: { item: InboxItem; active: boolean; onSelect: () => void; checked?: boolean; onCheck?: () => void }) {
  const who = item.contact ? fullName(item.contact) : item.from_name || item.from_email || "Unknown sender";
  return (
    <li className={cn("flex border-b", active && "bg-muted")}>
      {onCheck && (
        <div className="flex items-start pl-3 pt-3">
          <Checkbox aria-label={`Select conversation with ${who}`} checked={!!checked} onCheckedChange={onCheck} />
        </div>
      )}
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        className={cn(
          "grid min-w-0 flex-1 gap-0.5 px-3 py-2.5 text-left text-sm outline-none transition-colors hover:bg-muted/60 focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className={cn("flex min-w-0 items-center gap-1.5 truncate", !item.is_read && "font-semibold")}>
            {!item.is_read && <span className="size-2 shrink-0 rounded-full bg-primary" aria-label="Unread" />}
            <span className="truncate">{who}</span>
          </span>
          <time className="shrink-0 text-xs text-muted-foreground" dateTime={item.last_message_at ?? undefined}>
            {relTime(item.last_message_at)}
          </time>
        </div>
        <div className={cn("truncate", item.is_read ? "text-muted-foreground" : "text-foreground")}>{item.subject || "(no subject)"}</div>
        <div className="flex items-center gap-1.5">
          {item.intent && <StatusBadge status={item.intent} className="h-4 px-1 text-[10px]" />}
          {item.kind === "auto_reply" && <StatusBadge status="auto_reply" className="h-4 px-1 text-[10px]" />}
          <span className="truncate text-xs text-muted-foreground">{item.snippet}</span>
        </div>
      </button>
    </li>
  );
}

function ThreadView({ id, canWrite, onBack, onDelete }: { id: string; canWrite: boolean; onBack: () => void; onDelete: () => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = React.useState("");
  const thread = useQuery({ queryKey: ["inbox-thread", id], queryFn: () => api<Thread>(`/api/inbox/${id}`) });
  const patch = useMutation({
    mutationFn: (body: { is_read?: boolean; intent?: string | null }) => api(`/api/inbox/${id}`, { method: "PATCH", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["inbox-thread", id] });
      qc.invalidateQueries({ queryKey: ["inbox-unread"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reply = useMutation({
    mutationFn: () => api<{ message: string }>(`/api/inbox/${id}/reply`, { body: { body: draft } }),
    onSuccess: (r) => {
      toast.success(r.message);
      setDraft("");
      qc.invalidateQueries({ queryKey: ["inbox-thread", id] });
      qc.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  // Opening an unread conversation marks it read (writers only; viewers can't change state).
  const t = thread.data;
  const { mutate } = patch;
  React.useEffect(() => {
    if (t && !t.is_read && canWrite) mutate({ is_read: true });
  }, [t, canWrite, mutate]);

  if (thread.isError) return <ErrorState error={thread.error} onRetry={() => thread.refetch()} />;
  if (thread.isPending)
    return (
      <div className="grid gap-3 p-4">
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-40" />
      </div>
    );
  const c = t!.contact;
  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b p-3">
        <div className="flex min-w-0 items-start gap-2">
          <Button variant="ghost" size="icon-sm" className="lg:hidden" onClick={onBack} aria-label="Back to conversations">
            <ArrowLeft />
          </Button>
          <div className="min-w-0">
            <h2 className="truncate font-semibold">{t!.subject || "(no subject)"}</h2>
            <div className="text-xs text-muted-foreground">
              {c ? (
                <Link href={`/prospects/${c.id}`} className="text-signal hover:underline">
                  {fullName(c)}
                </Link>
              ) : (
                t!.messages.find((m) => m.direction === "in")?.from_email ?? "Unknown sender"
              )}
              {c?.company_name && ` · ${c.company_name}`}
              {t!.campaign && (
                <>
                  {" · "}
                  <Link href={`/campaigns/${t!.campaign.id}`} className="hover:underline">
                    {t!.campaign.name}
                  </Link>
                </>
              )}
              {c && (
                <>
                  {" · "}
                  <StatusBadge status={c.lead_status} className="h-4 px-1 text-[10px]" />
                </>
              )}
            </div>
          </div>
        </div>
        {canWrite && (
          <div className="flex items-center gap-1">
            <NativeSelect aria-label="Label reply" value={t!.intent ?? ""} onChange={(e) => patch.mutate({ intent: e.target.value || null })}>
              <option value="">Label…</option>
              {INTENTS.map((i) => (
                <option key={i} value={i}>
                  {humanize(i)}
                </option>
              ))}
            </NativeSelect>
            <Button variant="ghost" size="icon-sm" onClick={() => patch.mutate({ is_read: false })} aria-label="Mark unread">
              <MailOpen />
            </Button>
            <Button variant="ghost" size="icon-sm" onClick={onDelete} aria-label="Delete conversation" title="Delete from inbox">
              <Trash2 />
            </Button>
          </div>
        )}
      </div>
      <ol className="grid gap-3 p-3">
        {t!.messages.map((m) => (
          <li key={m.id} className={cn("rounded-lg border p-3", m.direction === "out" ? "bg-muted/40" : "bg-card")}>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
              <span>
                <span className="font-medium text-foreground">{m.direction === "out" ? `You (${m.from_email})` : m.from_name || m.from_email}</span>
                {m.direction === "in" && m.from_name && <span className="text-muted-foreground"> &lt;{m.from_email}&gt;</span>}
                {m.kind === "auto_reply" && <StatusBadge status="auto_reply" className="ml-1 h-4 px-1 text-[10px]" />}
                {m.kind === "manual_reply" && <span className="ml-1 text-muted-foreground">manual reply</span>}
              </span>
              <time className="text-muted-foreground" dateTime={m.at} title={dateTime(m.at)}>
                {relTime(m.at)}
              </time>
            </div>
            {m.html ? (
              <EmailFrame html={m.html} title={`Message from ${m.from_email}`} autoHeight />
            ) : (
              <p className="whitespace-pre-wrap text-sm">{m.text}</p>
            )}
          </li>
        ))}
      </ol>
      {canWrite && c && (
        <form
          className="mt-auto grid gap-2 border-t p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.trim()) reply.mutate();
          }}
        >
          <Textarea aria-label="Reply" rows={4} placeholder={`Reply to ${fullName(c)}…`} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              From {t!.mailbox?.email}
              {t!.mailbox?.provider === "sandbox" && " · sandbox: written to the local outbox, not sent"}
            </span>
            <Button type="submit" disabled={reply.isPending || !draft.trim()}>
              {reply.isPending ? <Spinner className="text-primary-foreground" /> : <Send />} Send reply
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

export default function InboxPageView() {
  const canWrite = useCanWrite();
  const [view, setView] = React.useState("all");
  const [mailbox, setMailbox] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [q, setQ] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [selected, setSelected] = React.useState<string | null>(null);
  React.useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const resetKey = `${view}|${mailbox}|${q}`;
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setPage(1);
  }
  const boxes = useQuery({ queryKey: ["email-accounts"], queryFn: () => api<EmailAccount[]>("/api/email-accounts") });
  const list = useQuery({
    queryKey: ["inbox", view, mailbox, q, page],
    queryFn: () => api<InboxPage>("/api/inbox", { query: { view, mailbox_id: mailbox, q, page, page_size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  const items = list.data?.items ?? [];
  const qc = useQueryClient();
  const [checked, setChecked] = React.useState<Set<string>>(new Set());
  const [confirming, setConfirming] = React.useState<string[] | null>(null);
  const toggle = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const remove = useMutation({
    mutationFn: (ids: string[]) => api<{ deleted: number }>("/api/inbox/delete", { body: { conversation_ids: ids } }),
    onSuccess: (r, ids) => {
      toast.success(`${r.deleted} conversation${r.deleted === 1 ? "" : "s"} removed from the inbox`);
      setChecked((prev) => new Set([...prev].filter((x) => !ids.includes(x))));
      if (selected && ids.includes(selected)) setSelected(null);
      setConfirming(null);
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["inbox-unread"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const visibleChecked = items.filter((i) => checked.has(i.id)).map((i) => i.id);

  return (
    <>
      <PageHeader title="Unified Inbox" description={list.data ? `${list.data.unread} unread campaign replies across all mailboxes` : "Replies to your campaigns from every connected mailbox."} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" placeholder="Search replies…" aria-label="Search inbox" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <NativeSelect aria-label="View" value={view} onChange={(e) => setView(e.target.value)}>
          {VIEWS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </NativeSelect>
        {(boxes.data?.length ?? 0) > 1 && (
          <NativeSelect aria-label="Mailbox" value={mailbox} onChange={(e) => setMailbox(e.target.value)}>
            <option value="">All mailboxes</option>
            {boxes.data!.map((b) => (
              <option key={b.id} value={b.id}>
                {b.email}
              </option>
            ))}
          </NativeSelect>
        )}
      </div>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-96" />
      ) : items.length === 0 && !selected ? (
        <EmptyState
          icon={Inbox}
          title={view === "all" && !q && !mailbox ? "No replies yet" : "Nothing here"}
          description={view === "all" && !q && !mailbox ? "Replies to your campaigns appear here as mailboxes sync (every 2 minutes)." : "Try another view or search."}
        />
      ) : (
        <div className="grid overflow-hidden rounded-xl border bg-card lg:h-[calc(100vh-14rem)] lg:grid-cols-[22rem_minmax(0,1fr)]">
          <div className={cn("flex min-h-0 flex-col border-r", selected && "hidden lg:flex")}>
            {canWrite && items.length > 0 && (
              <div className="flex min-h-10 items-center gap-2 border-b px-3 py-1.5 text-xs">
                <Checkbox
                  aria-label="Select all conversations on this page"
                  checked={visibleChecked.length === items.length}
                  indeterminate={visibleChecked.length > 0 && visibleChecked.length < items.length}
                  onCheckedChange={(on) => setChecked(on ? new Set(items.map((i) => i.id)) : new Set())}
                />
                {visibleChecked.length > 0 ? (
                  <>
                    <span className="text-muted-foreground">{visibleChecked.length} selected</span>
                    <Button variant="ghost" size="xs" className="ml-auto text-destructive" onClick={() => setConfirming(visibleChecked)}>
                      <Trash2 /> Delete
                    </Button>
                  </>
                ) : (
                  <span className="text-muted-foreground">Select to delete</span>
                )}
              </div>
            )}
            <ul className={cn("min-h-0 flex-1 overflow-y-auto", list.isPlaceholderData && "opacity-60")} aria-label="Conversations">
              {items.map((i) => (
                <Row key={i.id} item={i} active={i.id === selected} onSelect={() => setSelected(i.id)}
                  checked={checked.has(i.id)} onCheck={canWrite ? () => toggle(i.id) : undefined} />
              ))}
            </ul>
            {list.data.total > PAGE_SIZE && (
              <div className="border-t px-3">
                <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
              </div>
            )}
          </div>
          <div className={cn("min-h-0 overflow-y-auto", !selected && "hidden lg:block")}>
            {selected ? (
              <ThreadView key={selected} id={selected} canWrite={canWrite} onBack={() => setSelected(null)} onDelete={() => setConfirming([selected])} />
            ) : (
              <div className="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">Select a conversation</div>
            )}
          </div>
        </div>
      )}
      <ConfirmDialog
        open={!!confirming}
        onOpenChange={(o) => !o && setConfirming(null)}
        title={confirming && confirming.length > 1 ? `Delete ${confirming.length} conversations?` : "Delete this conversation?"}
        description="It is removed from the Trace CRM inbox and stays hidden after future syncs. Nothing is deleted in Gmail, and the prospect’s history, campaign stats and analytics are kept."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() => confirming && remove.mutate(confirming)}
      />
    </>
  );
}
