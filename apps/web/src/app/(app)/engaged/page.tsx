"use client";

import * as React from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Flame, ListPlus, NotebookPen, PhoneCall, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorState, NativeSelect, PageHeader, Pagination } from "@/components/common";
import { useCanWrite, useMe } from "@/components/app-shell";
import { OwnerSelect, useTeam, memberName } from "@/components/team";
import { LEVEL_HELP, LevelBadge, etTime } from "@/components/engagement";
import { OpportunityPanel, type PanelTab } from "@/components/opportunities/opportunity-panel";
import { api, errorMessage } from "@/lib/api";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Campaign, EngagedPage, EngagedProspect, EngagementLevel, Stage } from "@/lib/types";

const PAGE_SIZE = 50;
const SORTS: [string, string][] = [
  ["last_engagement", "Most recent engagement"], ["demo_clicks", "Demo clicks"], ["demo_visits", "Demo visits"],
  ["demo_completed", "Completed demo applications"], ["watched_video", "Watched the video"], ["replies", "Replies"],
  ["clicks", "All clicks"],
];

function Count({ n, strong }: { n: number; strong?: boolean }) {
  return <span className={cn("tabular-nums", n === 0 ? "text-muted-foreground" : strong && "font-semibold text-signal")}>{num(n)}</span>;
}

export default function EngagedProspectsPage() {
  const qc = useQueryClient();
  const canWrite = useCanWrite();
  const me = useMe();
  const team = useTeam();
  const [search, setSearch] = React.useState("");
  const [q, setQ] = React.useState("");
  const [level, setLevel] = React.useState<"" | EngagementLevel>("");
  const [campaign, setCampaign] = React.useState("");
  const [owner, setOwner] = React.useState("");
  const [sort, setSort] = React.useState("last_engagement");
  const [page, setPage] = React.useState(1);
  const [panel, setPanel] = React.useState<{ id: string; tab: PanelTab } | null>(null);
  React.useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const resetKey = [q, level, campaign, owner, sort].join("|");
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setPage(1);
  }

  const campaigns = useQuery({ queryKey: ["campaigns", "", false], queryFn: () => api<Campaign[]>("/api/campaigns") });
  const stages = useQuery({ queryKey: ["opportunity-stages"], queryFn: () => api<Stage[]>("/api/opportunities/stages") });
  const list = useQuery({
    queryKey: ["engaged", resetKey, page],
    queryFn: () => api<EngagedPage>("/api/tracking/engaged", {
      query: { q, level, campaign_id: campaign, owner, sort, page, page_size: PAGE_SIZE },
    }),
    placeholderData: keepPreviousData,
  });
  const assign = useMutation({
    mutationFn: ({ id, ownerId }: { id: string; ownerId: string | null }) =>
      api(`/api/opportunities/${id}`, { method: "PATCH", body: { owner_id: ownerId } }),
    onSuccess: () => {
      toast.success("Assignment saved");
      qc.invalidateQueries({ queryKey: ["engaged"] });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const data = list.data;
  const open = (p: EngagedProspect, tab: PanelTab = "engagement") => setPanel({ id: p.contact_id, tab });
  const filtered = !!(q || level || campaign || owner);
  return (
    <>
      <PageHeader title="Engaged Prospects"
        description="Prospects who did something measurable after your emails or on trace-studio.tech. Anyone who asked for a call back is listed first. These are engagement signals, not proof of buying intent." />

      <div role="tablist" aria-label="Engagement level" className="mb-3 flex flex-wrap gap-1.5">
        {(["", "high", "medium", "low"] as const).map((lv) => {
          const count = lv ? data?.levels[lv] : data ? Object.values(data.levels).reduce((a, b) => a + b, 0) : undefined;
          return (
            <button key={lv || "all"} type="button" role="tab" aria-selected={level === lv} onClick={() => setLevel(lv)}
              title={lv ? LEVEL_HELP[lv] : undefined}
              className={cn("rounded-full border px-3 py-1 text-sm transition-colors",
                level === lv ? "border-foreground bg-foreground text-background" : "hover:bg-muted")}>
              {lv ? `${lv[0].toUpperCase()}${lv.slice(1)}` : "All"}
              {count != null && <span className="ml-1.5 tabular-nums opacity-70">{num(count)}</span>}
            </button>
          );
        })}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" placeholder="Prospect or company…" aria-label="Search prospects" className="pl-8"
            value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <NativeSelect aria-label="Campaign" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
          <option value="">All campaigns</option>
          {(campaigns.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Assigned to" value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Anyone</option>
          <option value="me">Assigned to me</option>
          <option value="unassigned">Unassigned</option>
          {(team.data ?? []).filter((m) => m.is_active).map((m) => <option key={m.id} value={m.id}>{memberName(m)}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </NativeSelect>
      </div>

      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-64" />
      ) : data!.items.length === 0 ? (
        <EmptyState icon={Flame} title={filtered ? "No matches" : "No engaged prospects yet"}
          description={filtered ? undefined : "Prospects appear here when they click a tracked link, visit or use the demo on trace-studio.tech, or reply. Emails sent before click tracking started can't show clicks."} />
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Prospect</TableHead>
                  <TableHead>Level</TableHead>
                  <TableHead className="text-right">Demo clicks</TableHead>
                  <TableHead className="hidden sm:table-cell text-right">Demo visits</TableHead>
                  <TableHead className="text-right">Completed</TableHead>
                  <TableHead className="hidden sm:table-cell text-right" title="Watched at least 75% of the demo video">Watched video</TableHead>
                  <TableHead className="hidden md:table-cell text-right">Replies</TableHead>
                  <TableHead className="hidden lg:table-cell">Latest engagement</TableHead>
                  <TableHead className="hidden xl:table-cell">Campaigns</TableHead>
                  <TableHead className="hidden md:table-cell">Assigned to</TableHead>
                  <TableHead className="w-20"><span className="sr-only">Actions</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {data!.items.map((p) => (
                  <TableRow key={p.contact_id}>
                    <TableCell className="max-w-56">
                      <button type="button" onClick={() => open(p)} className="block max-w-full truncate text-left font-medium hover:underline">
                        {p.name || p.email}
                      </button>
                      <div className="truncate text-xs text-muted-foreground">{p.name ? p.email : null}{p.company && ` · ${p.company}`}</div>
                      {p.callback_requested_at && (
                        <div className="mt-0.5 inline-flex items-center gap-1 rounded-full bg-signal/15 px-2 py-0.5 text-xs font-medium text-signal"
                          title={`Asked for a call back ${etTime(p.callback_requested_at)}`}>
                          <PhoneCall className="size-3" aria-hidden /> Call back requested
                        </div>
                      )}
                    </TableCell>
                    <TableCell><LevelBadge level={p.level} /></TableCell>
                    <TableCell className="text-right"><Count n={p.demo_clicks} /></TableCell>
                    <TableCell className="hidden text-right sm:table-cell"><Count n={p.demo_visits} /></TableCell>
                    <TableCell className="text-right"><Count n={p.demo_completed} strong /></TableCell>
                    <TableCell className="hidden text-right sm:table-cell"><Count n={p.watched_video} /></TableCell>
                    <TableCell className="hidden text-right md:table-cell"><Count n={p.replies} /></TableCell>
                    <TableCell className="hidden whitespace-nowrap text-sm lg:table-cell">{etTime(p.last_engagement_at)}</TableCell>
                    <TableCell className="hidden max-w-48 truncate text-sm text-muted-foreground xl:table-cell">
                      {p.campaigns.map((c) => c.name).join(", ") || "—"}
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      {canWrite ? (
                        <OwnerSelect aria-label={`Assign ${p.name || p.email}`} value={p.owner?.id ?? null} meId={me.data?.user.id}
                          current={p.owner ? { ...p.owner, is_active: true } : null}
                          onChange={(ownerId) => assign.mutate({ id: p.contact_id, ownerId })} className="h-8 w-40" />
                      ) : <span className="text-sm">{p.owner?.name ?? "Unassigned"}</span>}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-0.5">
                        <Button variant="ghost" size="icon-sm" aria-label={`Follow-up task for ${p.name || p.email}`} title="Tasks" onClick={() => open(p, "tasks")}>
                          <ListPlus />{p.open_tasks > 0 && <span className="sr-only">{p.open_tasks} open</span>}
                        </Button>
                        <Button variant="ghost" size="icon-sm" aria-label={`Notes for ${p.name || p.email}`} title="Notes" onClick={() => open(p, "notes")}>
                          <NotebookPen />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data!.total} onPage={setPage} />
        </>
      )}

      <OpportunityPanel
        contactId={panel?.id ?? null}
        tab={panel?.tab ?? "engagement"}
        stages={stages.data ?? []}
        canWrite={canWrite}
        onTab={(t) => panel && setPanel({ ...panel, tab: t })}
        onClose={() => { setPanel(null); qc.invalidateQueries({ queryKey: ["engaged"] }); }}
      />
    </>
  );
}
