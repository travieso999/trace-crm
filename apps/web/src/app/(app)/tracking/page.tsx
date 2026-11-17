"use client";

import * as React from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Info, MousePointerClick, PlayCircle, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, ErrorState, NativeSelect, PageHeader, Pagination } from "@/components/common";
import { etTime } from "@/components/engagement";
import { api } from "@/lib/api";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Campaign, EmailTrackingPage, EmailTrackingRow } from "@/lib/types";

const PAGE_SIZE = 50;

function Stat({ label, value, hint, strong }: { label: string; value: number; hint?: string; strong?: boolean }) {
  return (
    <div className="rounded-xl border px-4 py-3" title={hint}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-2xl font-semibold tabular-nums", strong && "text-signal")}>{num(value)}</div>
    </div>
  );
}

function Demo({ r }: { r: EmailTrackingRow }) {
  if (!r.tracked) return <span className="text-muted-foreground">—</span>;
  const parts = [
    r.demo_clicks > 0 && `${r.demo_clicks} click${r.demo_clicks === 1 ? "" : "s"}`,
    r.demo_visits > 0 && `${r.demo_visits} visit${r.demo_visits === 1 ? "" : "s"}`,
    r.watched_video > 0 && "watched the video",
    r.demo_interactions > 0 && !r.demo_completed && "interacted",
  ].filter(Boolean);
  return (
    <div className="grid gap-0.5">
      {r.demo_completed > 0 && <span className="font-medium text-signal">Completed application</span>}
      <span className="text-muted-foreground">{parts.join(" · ") || "No demo activity"}</span>
    </div>
  );
}

export default function EmailTrackingPage() {
  const [search, setSearch] = React.useState("");
  const [q, setQ] = React.useState("");
  const [campaign, setCampaign] = React.useState("");
  const [from, setFrom] = React.useState("");
  const [to, setTo] = React.useState("");
  const [clicked, setClicked] = React.useState("");
  const [demo, setDemo] = React.useState("");
  const [sort, setSort] = React.useState("sent");
  const [page, setPage] = React.useState(1);
  React.useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const resetKey = [q, campaign, from, to, clicked, demo, sort].join("|");
  const [prevKey, setPrevKey] = React.useState(resetKey);
  if (prevKey !== resetKey) {
    setPrevKey(resetKey);
    setPage(1);
  }

  const campaigns = useQuery({ queryKey: ["campaigns", "", false], queryFn: () => api<Campaign[]>("/api/campaigns") });
  const list = useQuery({
    queryKey: ["email-tracking", resetKey, page],
    queryFn: () => api<EmailTrackingPage>("/api/tracking/emails", {
      query: { q, campaign_id: campaign, date_from: from, date_to: to, clicked, demo, sort, page, page_size: PAGE_SIZE },
    }),
    placeholderData: keepPreviousData,
  });
  const data = list.data;
  const filtered = !!(q || campaign || from || to || clicked || demo);

  return (
    <>
      <PageHeader title="Email Tracking"
        description="Who clicked what in your campaign emails, and what they did on the demo afterwards." />

      {data && (
        <div className="mb-4 flex gap-2 rounded-xl border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>
            Click tracking is <strong className="text-foreground">{data.settings.click_tracking ? "on" : "off"}</strong>
            {" · "}open tracking is <strong className="text-foreground">{data.settings.open_tracking ? "on (estimates only)" : "off"}</strong>.
            {" "}Clicks from email security scanners are flagged and left out of the counts. Emails sent before tracking started
            show “Not tracked” and can’t be attributed. Delivery shows “Accepted” because mail providers don’t confirm inbox delivery.
          </p>
        </div>
      )}

      {data && (
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Emails" value={data.summary.emails} hint={`${num(data.summary.tracked)} with tracked links`} />
          <Stat label="Clicked" value={data.summary.clicked} hint="Emails with at least one counted click" />
          <Stat label="Demo clicked" value={data.summary.demo_clicked} />
          <Stat label="Demo visited" value={data.summary.demo_visited} hint="The demo reported activity from this visit" />
          <Stat label="Completed demo" value={data.summary.demo_completed} strong />
          <Stat label="Scanner clicks" value={data.summary.scanner_clicks} hint="Flagged as automated; not counted" />
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input type="search" placeholder="Recipient or company…" aria-label="Search recipients" className="pl-8"
            value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <NativeSelect aria-label="Campaign" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
          <option value="">All campaigns</option>
          {(campaigns.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </NativeSelect>
        <label className="grid gap-0.5 text-xs text-muted-foreground">Sent from
          <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" />
        </label>
        <label className="grid gap-0.5 text-xs text-muted-foreground">to
          <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" />
        </label>
        <NativeSelect aria-label="Clicked" value={clicked} onChange={(e) => setClicked(e.target.value)}>
          <option value="">Clicked or not</option>
          <option value="yes">Clicked</option>
          <option value="no">Not clicked</option>
          <option value="untracked">Not tracked</option>
        </NativeSelect>
        <NativeSelect aria-label="Demo engagement" value={demo} onChange={(e) => setDemo(e.target.value)}>
          <option value="">Any demo activity</option>
          <option value="clicked">Clicked the demo</option>
          <option value="visited">Visited the demo</option>
          <option value="watched">Watched the video</option>
          <option value="interacted">Interacted with the demo</option>
          <option value="completed">Completed the application</option>
          <option value="not_completed">Clicked or visited, no application</option>
        </NativeSelect>
        <NativeSelect aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="sent">Newest sent</option>
          <option value="last_click">Latest click</option>
          <option value="clicks">Most clicks</option>
        </NativeSelect>
      </div>

      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton className="h-64" />
      ) : data!.items.length === 0 ? (
        <EmptyState icon={MousePointerClick} title={filtered ? "No matches" : "No campaign emails yet"}
          description={filtered ? undefined : "Campaign emails appear here as they are sent."} />
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Recipient</TableHead>
                  <TableHead className="hidden md:table-cell">Campaign</TableHead>
                  <TableHead className="hidden sm:table-cell">Sent</TableHead>
                  <TableHead className="hidden lg:table-cell text-right">Opens</TableHead>
                  <TableHead className="text-right">Clicks</TableHead>
                  <TableHead className="hidden lg:table-cell">Links clicked</TableHead>
                  <TableHead className="hidden xl:table-cell">First / latest click</TableHead>
                  <TableHead>Demo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className={list.isPlaceholderData ? "opacity-60" : undefined}>
                {data!.items.map((r) => {
                  const clickedLinks = r.links.filter((l) => l.clicks > 0);
                  return (
                    <TableRow key={r.sent_message_id}>
                      <TableCell className="max-w-56">
                        <Link href={`/opportunities?open=${r.contact_id}&tab=engagement`} className="block truncate font-medium hover:underline">
                          {r.name || r.email}
                        </Link>
                        <div className="truncate text-xs text-muted-foreground">{r.name ? r.email : null}{r.company && ` · ${r.company}`}</div>
                      </TableCell>
                      <TableCell className="hidden max-w-48 md:table-cell">
                        <div className="truncate">{r.campaign ?? "—"}</div>
                        <div className="text-xs text-muted-foreground">{r.step ? `Step ${r.step}` : null}</div>
                      </TableCell>
                      <TableCell className="hidden whitespace-nowrap sm:table-cell">
                        <div>{etTime(r.sent_at)}</div>
                        <div className={cn("text-xs", r.delivery === "bounced" ? "text-destructive" : "text-muted-foreground")}>
                          {r.delivery === "bounced" ? "Bounced" : "Accepted"}{r.replied_at && " · Replied"}
                        </div>
                      </TableCell>
                      <TableCell className="hidden text-right tabular-nums lg:table-cell" title={r.opens == null ? "Not measured" : "Estimated"}>
                        {r.opens == null ? <span className="text-muted-foreground">—</span> : `~${num(r.opens)}`}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {r.tracked ? (
                          <>
                            <div className={cn(r.clicks > 0 && "font-medium")}>{num(r.clicks)}<span className="text-muted-foreground"> / {num(r.unique_clicks)}</span></div>
                            {r.scanner_clicks > 0 && <div className="text-xs text-muted-foreground" title="Flagged as automated">+{r.scanner_clicks} scanner</div>}
                          </>
                        ) : <span className="text-xs text-muted-foreground">Not tracked</span>}
                      </TableCell>
                      <TableCell className="hidden max-w-60 lg:table-cell">
                        {clickedLinks.length === 0 ? <span className="text-muted-foreground">—</span> : (
                          <ul className="grid gap-0.5 text-xs">
                            {clickedLinks.map((l) => (
                              <li key={l.id} className="flex items-center gap-1 truncate" title={l.url ?? undefined}>
                                {l.kind === "demo" && <PlayCircle className="size-3 shrink-0 text-signal" aria-label="Demo link" />}
                                <span className="truncate">{l.label || l.url}</span>
                                {l.clicks > 1 && <span className="text-muted-foreground">×{l.clicks}</span>}
                              </li>
                            ))}
                          </ul>
                        )}
                      </TableCell>
                      <TableCell className="hidden whitespace-nowrap text-xs xl:table-cell">
                        {r.first_click_at ? <><div>{etTime(r.first_click_at)}</div>
                          {r.last_click_at !== r.first_click_at && <div className="text-muted-foreground">{etTime(r.last_click_at)}</div>}</>
                          : <span className="text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell className="text-sm"><Demo r={r} /></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data!.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
