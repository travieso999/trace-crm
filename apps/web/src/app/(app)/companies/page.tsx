"use client";

import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Building2, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, NativeSelect, PageHeader, Pagination, StatusBadge } from "@/components/common";
import { CompanyForm, companyPayload, type CompanyValues } from "@/components/company-form";
import { useCanWrite } from "@/components/app-shell";
import { api, errorMessage } from "@/lib/api";
import { num, relTime } from "@/lib/format";
import type { Company, Page } from "@/lib/types";

const PAGE_SIZE = 50;

export default function CompaniesPage() {
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const [search, setSearch] = React.useState("");
  const [q, setQ] = React.useState("");
  const [websiteStatus, setWebsiteStatus] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [creating, setCreating] = React.useState(false);

  React.useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const resetKey = `${q}|${websiteStatus}`;
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setPage(1);
  }

  const list = useQuery({
    queryKey: ["companies", q, websiteStatus, page],
    queryFn: ({ signal }) =>
      api<Page<Company>>("/api/companies", { query: { q, website_status: websiteStatus, page, page_size: PAGE_SIZE }, signal }),
    placeholderData: keepPreviousData,
  });

  async function create(v: CompanyValues) {
    try {
      await api<Company>("/api/companies", { body: companyPayload(v) });
      toast.success("Company created");
      qc.invalidateQueries({ queryKey: ["companies"] });
      setCreating(false);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  const items = list.data?.items ?? [];
  const filtered = !!(q || websiteStatus);

  return (
    <>
      <PageHeader
        title="Companies"
        description={list.data ? `${num(list.data.total)} ${filtered ? "matching" : "total"}` : undefined}
        actions={
          canWrite && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> Add company
            </Button>
          )
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            placeholder="Search name, domain, city…"
            aria-label="Search companies"
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <NativeSelect aria-label="Website status" value={websiteStatus} onChange={(e) => setWebsiteStatus(e.target.value)}>
          <option value="">Any website status</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
          <option value="error">Error</option>
          <option value="unknown">Unknown</option>
        </NativeSelect>
      </div>

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
          icon={Building2}
          title={filtered ? "No companies match" : "No companies yet"}
          description={filtered ? "Try a different search." : "Companies are created automatically when you import leads."}
        />
      ) : (
        <>
          <div className="rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead className="hidden md:table-cell">Industry</TableHead>
                  <TableHead className="hidden md:table-cell">Location</TableHead>
                  <TableHead>Website</TableHead>
                  <TableHead className="text-right">Leads</TableHead>
                  <TableHead className="hidden lg:table-cell">Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {items.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="max-w-64">
                      <Link href={`/companies/${c.id}`} className="block truncate font-medium hover:underline">
                        {c.name}
                      </Link>
                      {c.domain && <div className="truncate text-xs text-muted-foreground">{c.domain}</div>}
                    </TableCell>
                    <TableCell className="hidden md:table-cell">{c.industry ?? "—"}</TableCell>
                    <TableCell className="hidden md:table-cell">{[c.city, c.state].filter(Boolean).join(", ") || "—"}</TableCell>
                    <TableCell>
                      <StatusBadge status={c.website_status} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{num(c.contact_count)}</TableCell>
                    <TableCell className="hidden text-muted-foreground lg:table-cell">{relTime(c.updated_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        </>
      )}

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Add company</DialogTitle>
          </DialogHeader>
          <CompanyForm submitLabel="Create company" onSubmit={create} onCancel={() => setCreating(false)} />
        </DialogContent>
      </Dialog>
    </>
  );
}
