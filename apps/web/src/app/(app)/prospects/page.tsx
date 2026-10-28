"use client";

import * as React from "react";
import { cn } from "cn";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Download, Plus, Search, Send, Upload, Users, X } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog, EmptyState, ErrorState, NativeSelect, PageHeader, Pagination, StatusBadge } from "@/components/common";
import { LeadForm, toPayload } from "@/components/lead-form";
import { EnrollDialog } from "@/components/enroll-dialog";
import { useCanWrite } from "@/components/app-shell";
import { api, download, errorMessage } from "@/lib/api";
import { fullName, humanize, num, relTime } from "@/lib/format";
import { LEAD_STATUSES, type Contact, type Page, type Tag, type ImportList } from "@/lib/types";

const PAGE_SIZE = 50;

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

type Filters = { lead_status: string; verification_status: string; tag: string; batch_id: string; state: string; city: string; industry: string };

const NO_FILTERS: Filters = { lead_status: "", verification_status: "", tag: "", batch_id: "", state: "", city: "", industry: "" };
type Sort = { key: string; order: "asc" | "desc" };

function SortHead({ label, k, sort, onSort, className }: { label: string; k: string; sort: Sort; onSort: (s: Sort) => void; className?: string }) {
  const active = sort.key === k;
  return (
    <TableHead className={className} aria-sort={active ? (sort.order === "asc" ? "ascending" : "descending") : undefined}>
      <button
        type="button"
        className="inline-flex items-center gap-1 hover:text-foreground"
        onClick={() => onSort({ key: k, order: active && sort.order === "asc" ? "desc" : "asc" })}
      >
        {label}
        {active && (sort.order === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
      </button>
    </TableHead>
  );
}

function BulkBar({ ids, tags, onDone }: { ids: string[]; tags: Tag[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [enrolling, setEnrolling] = React.useState(false);
  const bulk = useMutation({
    mutationFn: (body: { action: string; value?: string }) => api<{ updated: number }>("/api/contacts/bulk", { body: { ids, ...body } }),
    onSuccess: (r, vars) => {
      toast.success(`${humanize(vars.action)}: ${num(r.updated)} prospect${r.updated === 1 ? "" : "s"} updated`);
      qc.invalidateQueries({ queryKey: ["contacts"] });
      qc.invalidateQueries({ queryKey: ["contact-tags"] });
      setConfirmDelete(false);
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const [tagDraft, setTagDraft] = React.useState("");
  function addTag(e: React.FormEvent) {
    e.preventDefault();
    const name = tagDraft.trim();
    if (name) bulk.mutate({ action: "add_tag", value: name }, { onSuccess: () => setTagDraft("") });
  }

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/50 px-3 py-2 text-sm" role="region" aria-label="Bulk actions">
      <span className="font-medium">{num(ids.length)} selected</span>
      <NativeSelect
        aria-label="Set prospect status"
        value=""
        disabled={bulk.isPending}
        onChange={(e) => e.target.value && bulk.mutate({ action: "set_status", value: e.target.value })}
      >
        <option value="">Set status…</option>
        {LEAD_STATUSES.map((s) => (
          <option key={s} value={s}>
            {humanize(s)}
          </option>
        ))}
      </NativeSelect>
      <NativeSelect
        aria-label="Set verification"
        value=""
        disabled={bulk.isPending}
        onChange={(e) => e.target.value && bulk.mutate({ action: "set_verification", value: e.target.value })}
      >
        <option value="">Set verification…</option>
        {["valid", "risky", "invalid", "unknown"].map((s) => (
          <option key={s} value={s}>
            {humanize(s)}
          </option>
        ))}
      </NativeSelect>
      <Button variant="outline" size="sm" disabled={bulk.isPending} onClick={() => setEnrolling(true)}>
        <Send /> Add to campaign…
      </Button>
      <EnrollDialog contactIds={ids} open={enrolling} onOpenChange={setEnrolling} onDone={onDone} />
      <form onSubmit={addTag} className="flex items-center gap-1">
        <Input
          aria-label="Tag to add"
          placeholder="Tag name"
          className="h-7 w-28"
          value={tagDraft}
          onChange={(e) => setTagDraft(e.target.value)}
        />
        <Button type="submit" variant="outline" size="sm" disabled={bulk.isPending || !tagDraft.trim()}>
          Add tag
        </Button>
      </form>
      {tags.length > 0 && (
        <NativeSelect
          aria-label="Remove tag"
          value=""
          disabled={bulk.isPending}
          onChange={(e) => e.target.value && bulk.mutate({ action: "remove_tag", value: e.target.value })}
        >
          <option value="">Remove tag…</option>
          {tags.map((t) => (
            <option key={t.id} value={t.name}>
              {t.name}
            </option>
          ))}
        </NativeSelect>
      )}
      <Button variant="destructive" size="sm" onClick={() => setConfirmDelete(true)} disabled={bulk.isPending}>
        Delete
      </Button>
      <Button variant="ghost" size="icon-sm" className="ml-auto" onClick={onDone} aria-label="Clear selection">
        <X />
      </Button>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${num(ids.length)} prospect${ids.length === 1 ? "" : "s"}?`}
        description="They are removed from all campaigns and pending sends are cancelled."
        confirmLabel="Delete"
        destructive
        pending={bulk.isPending}
        onConfirm={() => bulk.mutate({ action: "delete" })}
      />
    </div>
  );
}

function ProspectsList() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const [search, setSearch] = React.useState("");
  const q = useDebounced(search.trim());
  // ?batch_id= opens the prospects of one imported list (from Imported Lists)
  const [filters, setFilters] = React.useState<Filters>({ ...NO_FILTERS, batch_id: params.get("batch_id") ?? "" });
  const [place, setPlace] = React.useState({ state: "", city: "", industry: "" });
  const placeQ = useDebounced(JSON.stringify(place));
  const lists = useQuery({ queryKey: ["import-lists"], queryFn: () => api<ImportList[]>("/api/imports") });
  const [sort, setSort] = React.useState<Sort>({ key: "created_at", order: "desc" });
  const [page, setPage] = React.useState(1);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [creating, setCreating] = React.useState(false);

  const query = { q, ...filters, ...(JSON.parse(placeQ) as typeof place), sort: sort.key, order: sort.order };
  const list = useQuery({
    queryKey: ["contacts", query, page],
    queryFn: ({ signal }) => api<Page<Contact>>("/api/contacts", { query: { ...query, page, page_size: PAGE_SIZE }, signal }),
    placeholderData: keepPreviousData,
  });
  const tags = useQuery({ queryKey: ["contact-tags"], queryFn: () => api<Tag[]>("/api/contacts/tags") });

  // Any change to the result set resets paging and selection.
  const resetKey = JSON.stringify(query);
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setPage(1);
    setSelected(new Set());
  }

  const items = list.data?.items ?? [];
  const allOnPage = items.length > 0 && items.every((c) => selected.has(c.id));
  const filtered = !!(q || Object.values(filters).some(Boolean) || Object.values(place).some(Boolean));
  const listName = lists.data?.find((l) => l.id === filters.batch_id)?.name;

  function toggle(id: string) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  async function exportCsv() {
    try {
      await download("/api/contacts/export", { q, ...filters, ...place }, "prospects.csv");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function create(values: Parameters<typeof toPayload>[0]) {
    try {
      const c = await api<Contact>("/api/contacts", { body: toPayload(values) });
      toast.success("Prospect created");
      qc.invalidateQueries({ queryKey: ["contacts"] });
      setCreating(false);
      router.push(`/prospects/${c.id}`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <>
      <PageHeader
        title="Prospects"
        description={list.data ? `${num(list.data.total)} ${filtered ? "matching" : "total"}${listName ? ` in the list “${listName}”` : ""}` : undefined}
        actions={
          <>
            {canWrite && (
              <>
                <Button variant="outline" onClick={exportCsv}>
                  <Download /> Export
                </Button>
                <Link href="/prospects/import" className={cn(buttonVariants({ variant: "outline" }))}>
                  <Upload /> Import CSV
                </Link>
                <Button onClick={() => setCreating(true)}>
                  <Plus /> Add prospect
                </Button>
              </>
            )}
          </>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            placeholder="Search name, email, company…"
            aria-label="Search prospects"
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <NativeSelect aria-label="Prospect status" value={filters.lead_status} onChange={(e) => setFilters({ ...filters, lead_status: e.target.value })}>
          <option value="">All statuses</option>
          {LEAD_STATUSES.map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label="Verification"
          value={filters.verification_status}
          onChange={(e) => setFilters({ ...filters, verification_status: e.target.value })}
        >
          <option value="">Any verification</option>
          {["valid", "risky", "invalid", "unknown"].map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </NativeSelect>
        {!!lists.data?.length && (
          <NativeSelect aria-label="Imported list" value={filters.batch_id} onChange={(e) => setFilters({ ...filters, batch_id: e.target.value })}>
            <option value="">All lists</option>
            {lists.data.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} ({num(l.contacts)})
              </option>
            ))}
          </NativeSelect>
        )}
        <Input aria-label="State" placeholder="State" className="w-20" value={place.state} onChange={(e) => setPlace({ ...place, state: e.target.value })} />
        <Input aria-label="City" placeholder="City" className="w-28" value={place.city} onChange={(e) => setPlace({ ...place, city: e.target.value })} />
        <Input aria-label="Industry" placeholder="Industry" className="w-32" value={place.industry} onChange={(e) => setPlace({ ...place, industry: e.target.value })} />
        {!!tags.data?.length && (
          <NativeSelect aria-label="Tag" value={filters.tag} onChange={(e) => setFilters({ ...filters, tag: e.target.value })}>
            <option value="">Any tag</option>
            {tags.data.map((t) => (
              <option key={t.id} value={t.name}>
                {t.name}
              </option>
            ))}
          </NativeSelect>
        )}
        {filtered && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearch("");
              setFilters(NO_FILTERS);
              setPlace({ state: "", city: "", industry: "" });
              if (params.get("batch_id")) router.replace("/prospects");
            }}
          >
            Clear filters
          </Button>
        )}
      </div>

      {canWrite && selected.size > 0 && <BulkBar ids={[...selected]} tags={tags.data ?? []} onDone={() => setSelected(new Set())} />}

      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <div className="grid gap-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={Users}
          title={filtered ? "No prospects match these filters" : "No prospects yet"}
          description={filtered ? "Try a different search or clear the filters." : "Import a CSV or add your first prospect."}
          action={
            !filtered && canWrite ? (
              <Link href="/prospects/import" className={cn(buttonVariants())}>
                <Upload /> Import CSV
              </Link>
            ) : undefined
          }
        />
      ) : (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  {canWrite && (
                    <TableHead className="w-10">
                      <Checkbox
                        aria-label="Select all on this page"
                        checked={allOnPage}
                        onCheckedChange={(v) =>
                          setSelected((s) => {
                            const n = new Set(s);
                            items.forEach((c) => (v ? n.add(c.id) : n.delete(c.id)));
                            return n;
                          })
                        }
                      />
                    </TableHead>
                  )}
                  <SortHead label="Name" k="last_name" sort={sort} onSort={setSort} />
                  <SortHead label="Company" k="company_name" sort={sort} onSort={setSort} className="hidden md:table-cell" />
                  <SortHead label="State" k="state" sort={sort} onSort={setSort} className="hidden lg:table-cell" />
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden lg:table-cell">Verification</TableHead>
                  <TableHead className="hidden xl:table-cell">Tags</TableHead>
                  <SortHead label="Last contacted" k="last_contacted_at" sort={sort} onSort={setSort} className="hidden md:table-cell" />
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {items.map((c) => (
                  <TableRow key={c.id} data-state={selected.has(c.id) ? "selected" : undefined}>
                    {canWrite && (
                      <TableCell>
                        <Checkbox aria-label={`Select ${c.email}`} checked={selected.has(c.id)} onCheckedChange={() => toggle(c.id)} />
                      </TableCell>
                    )}
                    <TableCell className="max-w-64">
                      <Link href={`/prospects/${c.id}`} className="block truncate font-medium hover:underline">
                        {fullName(c)}
                      </Link>
                      <div className="truncate text-xs text-muted-foreground">
                        {c.email}
                        {c.suppressed && <span className="ml-1.5 text-destructive">· suppressed</span>}
                      </div>
                    </TableCell>
                    <TableCell className="hidden max-w-48 truncate md:table-cell">{c.company_name ?? "—"}</TableCell>
                    <TableCell className="hidden lg:table-cell">{c.state ?? "—"}</TableCell>
                    <TableCell>
                      <StatusBadge status={c.lead_status} />
                    </TableCell>
                    <TableCell className="hidden lg:table-cell">
                      <StatusBadge status={c.verification_status} />
                    </TableCell>
                    <TableCell className="hidden xl:table-cell">
                      <div className="flex max-w-56 flex-wrap gap-1">
                        {c.tags.slice(0, 3).map((t) => (
                          <Badge key={t.id} variant="outline" className="font-normal">
                            {t.name}
                          </Badge>
                        ))}
                        {c.tags.length > 3 && <span className="text-xs text-muted-foreground">+{c.tags.length - 3}</span>}
                      </div>
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground md:table-cell">{relTime(c.last_contacted_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        </>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Add prospect</DialogTitle>
          </DialogHeader>
          <LeadForm submitLabel="Create prospect" onSubmit={create} onCancel={() => setCreating(false)} />
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function ProspectsPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-64" />}>
      <ProspectsList />
    </React.Suspense>
  );
}
