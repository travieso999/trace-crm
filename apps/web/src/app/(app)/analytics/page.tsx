"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, NativeSelect, PageHeader } from "@/components/common";
import { DailyBars, TrendLines } from "@/components/charts";
import { api } from "@/lib/api";
import { humanize, num, pct } from "@/lib/format";
import type { Analytics, Campaign, EmailAccount } from "@/lib/types";

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = React.useState(30);
  const [campaign, setCampaign] = React.useState("");
  const [mailbox, setMailbox] = React.useState("");
  const campaigns = useQuery({ queryKey: ["campaigns", "", false], queryFn: () => api<Campaign[]>("/api/campaigns") });
  const boxes = useQuery({ queryKey: ["email-accounts"], queryFn: () => api<EmailAccount[]>("/api/email-accounts") });
  const a = useQuery({
    queryKey: ["analytics", days, campaign, mailbox],
    queryFn: () => api<Analytics>("/api/analytics", { query: { days, campaign_id: campaign, mailbox_id: mailbox } }),
  });
  const s = a.data?.summary;

  return (
    <>
      <PageHeader
        title="Analytics"
        description={a.data ? `Last ${a.data.days} days · ${a.data.timezone}. “Sent” means accepted by the mail provider.` : undefined}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <NativeSelect aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 365].map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </NativeSelect>
        <NativeSelect aria-label="Campaign" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
          <option value="">All campaigns</option>
          {(campaigns.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect aria-label="Mailbox" value={mailbox} onChange={(e) => setMailbox(e.target.value)}>
          <option value="">All mailboxes</option>
          {(boxes.data ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.email}
            </option>
          ))}
        </NativeSelect>
      </div>
      {a.isError ? (
        <ErrorState error={a.error} onRetry={() => a.refetch()} />
      ) : a.isPending ? (
        <div className="grid gap-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-64" />
        </div>
      ) : !a.data.has_data && !campaign && !mailbox ? (
        <EmptyState
          icon={BarChart3}
          title="No sending activity in this period"
          description="Analytics fill in as campaigns send and replies arrive."
          action={
            <Link href="/campaigns" className="text-sm font-medium text-signal hover:underline">
              Go to campaigns
            </Link>
          }
        />
      ) : (
        <div className="grid gap-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Kpi label="Sent" value={num(s!.accepted)} sub={`${num(s!.failed)} failed`} />
            <Kpi label="Replies" value={num(s!.replied)} sub={`${pct(s!.reply_rate)} reply rate`} />
            <Kpi label="Positive" value={num(s!.positive_reply)} sub={`${pct(s!.positive_reply_rate)} of sent`} />
            <Kpi label="Meetings" value={num(s!.meeting_booked)} />
            <Kpi label="Bounced" value={num(s!.bounced)} sub={`${pct(s!.bounce_rate)} bounce rate`} />
            <Kpi label="Unsubscribed" value={num(s!.unsubscribed)} sub={`${pct(s!.unsubscribe_rate)}`} />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Emails sent</CardTitle>
                <CardDescription>Per day, accepted by the provider</CardDescription>
              </CardHeader>
              <CardContent>
                <DailyBars data={a.data.daily} series={{ key: "accepted", label: "Sent", color: "var(--series-1)" }} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Replies</CardTitle>
                <CardDescription>All replies vs. positive, per day</CardDescription>
              </CardHeader>
              <CardContent>
                <TrendLines
                  data={a.data.daily}
                  series={[
                    { key: "replied", label: "Replies", color: "var(--series-1)" },
                    { key: "positive_reply", label: "Positive", color: "var(--series-2)" },
                  ]}
                />
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>By campaign</CardTitle>
            </CardHeader>
            <CardContent className="px-0">
              {a.data.per_campaign.length === 0 ? (
                <p className="px-4 text-sm text-muted-foreground">No campaign activity in this period.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">Campaign</TableHead>
                      <TableHead className="text-right">Sent</TableHead>
                      <TableHead className="text-right">Replies</TableHead>
                      <TableHead className="hidden text-right sm:table-cell">Reply rate</TableHead>
                      <TableHead className="hidden text-right md:table-cell">Positive</TableHead>
                      <TableHead className="hidden pr-4 text-right md:table-cell">Bounce rate</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {a.data.per_campaign.map((r) => (
                      <TableRow key={r.campaign_id}>
                        <TableCell className="max-w-64 truncate pl-4">
                          <Link href={`/campaigns/${r.campaign_id}`} className="font-medium hover:underline">
                            {r.name}
                          </Link>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{num(r.accepted)}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(r.replied)}</TableCell>
                        <TableCell className="hidden text-right tabular-nums sm:table-cell">{pct(r.reply_rate)}</TableCell>
                        <TableCell className="hidden text-right tabular-nums md:table-cell">{num(r.positive_reply)}</TableCell>
                        <TableCell className="hidden pr-4 text-right tabular-nums md:table-cell">{pct(r.bounce_rate)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>By mailbox</CardTitle>
              </CardHeader>
              <CardContent className="px-0">
                {a.data.per_mailbox.length === 0 ? (
                  <p className="px-4 text-sm text-muted-foreground">No mailbox activity in this period.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="pl-4">Mailbox</TableHead>
                        <TableHead className="text-right">Sent</TableHead>
                        <TableHead className="text-right">Failed</TableHead>
                        <TableHead className="pr-4 text-right">Bounce rate</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {a.data.per_mailbox.map((r) => (
                        <TableRow key={r.email_account_id}>
                          <TableCell className="max-w-56 truncate pl-4">{r.email}</TableCell>
                          <TableCell className="text-right tabular-nums">{num(r.accepted)}</TableCell>
                          <TableCell className="text-right tabular-nums">{num(r.failed)}</TableCell>
                          <TableCell className="pr-4 text-right tabular-nums">{pct(r.bounce_rate)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Prospect stages</CardTitle>
                <CardDescription>All prospects, current stage</CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="grid gap-1.5 text-sm">
                  {a.data.funnel.map((f) => (
                    <li key={f.stage} className="flex justify-between gap-2">
                      <span className="text-muted-foreground">{humanize(f.stage)}</span>
                      <span className="tabular-nums">{num(f.count)}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
