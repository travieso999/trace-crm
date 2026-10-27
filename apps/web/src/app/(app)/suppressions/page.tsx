"use client";

import * as React from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Search, ShieldBan, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, Field, NativeSelect, PageHeader, Pagination } from "@/components/common";
import { useCanWrite, useIsAdmin } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { dateTime, humanize, num, relTime } from "@/lib/format";
import type { Page, Suppression } from "@/lib/types";

const REASONS = ["manual", "unsubscribe", "reply_request", "bounce", "complaint"];
const PAGE_SIZE = 50;

export default function SuppressionsPage() {
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const isAdmin = useIsAdmin();
  const [search, setSearch] = React.useState("");
  const [q, setQ] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [adding, setAdding] = React.useState(false);
  const [emails, setEmails] = React.useState("");
  const [addReason, setAddReason] = React.useState("manual");
  const [removing, setRemoving] = React.useState<Suppression | null>(null);
  React.useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const resetKey = `${q}|${reason}`;
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setPage(1);
  }

  const list = useQuery({
    queryKey: ["suppressions", q, reason, page],
    queryFn: () => api<Page<Suppression>>("/api/suppressions", { query: { q, reason, page, page_size: PAGE_SIZE } }),
    placeholderData: keepPreviousData,
  });
  const add = useMutation({
    mutationFn: () =>
      api<{ added: number; already_suppressed: number; invalid: string[]; enrollments_stopped: number }>("/api/suppressions", {
        body: { emails: emails.split(/[\s,;]+/).filter(Boolean), reason: addReason },
      }),
    onSuccess: (r) => {
      toast.success(`${num(r.added)} address${r.added === 1 ? "" : "es"} suppressed`, {
        description: [
          r.enrollments_stopped && `${r.enrollments_stopped} active enrollment(s) stopped`,
          r.already_suppressed && `${r.already_suppressed} already on the list`,
          r.invalid.length && `${r.invalid.length} invalid: ${r.invalid.slice(0, 3).join(", ")}`,
        ]
          .filter(Boolean)
          .join(" · ") || undefined,
      });
      qc.invalidateQueries({ queryKey: ["suppressions"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
      setEmails("");
      setAdding(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<{ message: string }>(`/api/suppressions/${id}`, { method: "DELETE" }),
    onSuccess: (r) => {
      toast.success(r.message);
      qc.invalidateQueries({ queryKey: ["suppressions"] });
      setRemoving(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const items = list.data?.items ?? [];
  const count = emails.split(/[\s,;]+/).filter(Boolean).length;
  return (
    <>
      <PageHeader
        title="Suppression List"
        description={list.data ? `${num(list.data.total)} address${list.data.total === 1 ? "" : "es"} that will never be emailed` : "Addresses that will never be emailed."}
        actions={
          canWrite && (
            <Button onClick={() => setAdding(true)}>
              <Plus /> Add addresses
            </Button>
          )
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" placeholder="Search email…" aria-label="Search suppressions" className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <NativeSelect aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="">Any reason</option>
          {REASONS.map((r) => (
            <option key={r} value={r}>
              {humanize(r)}
            </option>
          ))}
        </NativeSelect>
      </div>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-48" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={ShieldBan}
          title={q || reason ? "No matches" : "Nobody is suppressed yet"}
          description={q || reason ? undefined : "Unsubscribes, bounces and opt-out replies are added here automatically."}
        />
      ) : (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead className="hidden md:table-cell">Source</TableHead>
                  <TableHead className="hidden sm:table-cell">Added</TableHead>
                  {isAdmin && <TableHead className="w-10"><span className="sr-only">Actions</span></TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {items.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="max-w-64 truncate font-medium">{s.email}</TableCell>
                    <TableCell>{humanize(s.reason)}</TableCell>
                    <TableCell className="hidden max-w-56 truncate text-muted-foreground md:table-cell">{s.source ?? "—"}</TableCell>
                    <TableCell className="hidden text-muted-foreground sm:table-cell" title={dateTime(s.created_at)}>
                      {relTime(s.created_at)}
                    </TableCell>
                    {isAdmin && (
                      <TableCell>
                        <Button variant="ghost" size="icon-sm" aria-label={`Allow ${s.email} again`} onClick={() => setRemoving(s)}>
                          <Undo2 />
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        </>
      )}
      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Suppress addresses</DialogTitle>
            <DialogDescription>They’re removed from every active campaign immediately and can’t be enrolled again.</DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (count) add.mutate();
            }}
          >
            <Field label="Email addresses" htmlFor="sup-emails" hint="One per line, or separated by commas.">
              <Textarea id="sup-emails" rows={6} className="font-mono text-[13px]" value={emails} onChange={(e) => setEmails(e.target.value)} />
            </Field>
            <Field label="Reason" htmlFor="sup-reason">
              <NativeSelect id="sup-reason" value={addReason} onChange={(e) => setAddReason(e.target.value)}>
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {humanize(r)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setAdding(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={add.isPending || !count || count > 5000}>
                Suppress {count ? num(count) : ""}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Allow ${removing?.email} again?`}
        description={
          removing?.reason === "unsubscribe" || removing?.reason === "reply_request"
            ? "This person asked not to be contacted. Only remove them if they explicitly opted back in."
            : "They can be enrolled in campaigns again."
        }
        confirmLabel="Remove from list"
        destructive
        pending={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.id)}
      />
    </>
  );
}
