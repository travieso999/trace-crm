"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, History, Mail } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, PageHeader, StatusBadge } from "@/components/common";
import { DailyBars, TrendLines } from "@/components/charts";
import { api } from "@/lib/api";
import { dateTime, humanize, num, pct, relTime } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card size="sm" className="gap-1">
      <CardContent>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</div>
        {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  );
}

function Funnel({ stages }: { stages: Dashboard["funnel"] }) {
  const max = Math.max(1, ...stages.map((s) => s.count));
  return (
    <ul className="grid gap-2">
      {stages.map((s) => (
        <li key={s.stage} className="grid grid-cols-[8.5rem_1fr_3rem] items-center gap-3 text-sm">
          <span className="truncate text-muted-foreground">{humanize(s.stage)}</span>
          <span className="h-2 rounded-full bg-muted">
            <span
              className="block h-2 rounded-full"
              style={{ width: `${(s.count / max) * 100}%`, background: "var(--series-1)" }}
            />
          </span>
          <span className="text-right tabular-nums">{num(s.count)}</span>
        </li>
      ))}
    </ul>
  );
}

export default function OverviewPage() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/dashboard"), refetchInterval: 60_000 });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isPending) {
    return (
      <>
        <PageHeader title="Overview" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="mt-4 h-72" />
      </>
    );
  }

  const d = q.data;
  const k = d.kpis;
  return (
    <>
      <PageHeader title="Overview" description={`All times in ${d.timezone}. Rates cover the last 30 days.`} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Sent today" value={num(k.sent_today)} hint={`${num(k.queued_messages)} queued`} />
        <Stat label="Sent this month" value={num(k.sent_this_month)} />
        <Stat label="Reply rate" value={pct(k.reply_rate_30d)} hint={`${num(k.replies_total)} replies all time`} />
        <Stat label="Positive replies" value={num(k.positive_replies_total)} hint={`${num(k.meetings_booked)} meetings or later`} />
        <Stat label="Contacts" value={num(k.total_contacts)} />
        <Stat label="Active campaigns" value={num(k.active_campaigns)} />
        <Stat label="Bounce rate" value={pct(k.bounce_rate_30d)} />
        <Stat label="Unsubscribe rate" value={pct(k.unsubscribe_rate_30d)} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Emails sent</CardTitle>
            <CardDescription>Accepted by the provider, per day</CardDescription>
          </CardHeader>
          <CardContent>
            <DailyBars
              data={d.daily_volume}
              series={{ key: "accepted", label: "Sent", color: "var(--series-1)" }}
              extra={(row) =>
                Number(row.failed) + Number(row.bounced) > 0 ? (
                  <div className="mt-1 border-t pt-1 text-muted-foreground">
                    {num(Number(row.failed))} failed · {num(Number(row.bounced))} bounced
                  </div>
                ) : null
              }
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Replies</CardTitle>
            <CardDescription>All replies vs. positive, per day</CardDescription>
          </CardHeader>
          <CardContent>
            <TrendLines
              data={d.reply_trend}
              series={[
                { key: "replied", label: "Replies", color: "var(--series-1)" },
                { key: "positive_reply", label: "Positive", color: "var(--series-2)" },
              ]}
            />
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Lead pipeline</CardTitle>
          </CardHeader>
          <CardContent>
            <Funnel stages={d.funnel} />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Mailbox health</CardTitle>
          </CardHeader>
          <CardContent>
            {d.mailbox_health.length === 0 ? (
              <EmptyState
                icon={Mail}
                title="No mailboxes connected"
                action={
                  <Link href="/email-accounts" className="text-sm font-medium text-primary hover:underline">
                    Connect a mailbox
                  </Link>
                }
              />
            ) : (
              <ul className="divide-y">
                {d.mailbox_health.map((m) => (
                  <li key={m.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm">
                    <span className="min-w-0 flex-1 truncate font-medium">{m.email}</span>
                    {m.circuit_open && (
                      <span className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
                        <AlertTriangle className="size-3.5" aria-hidden /> Paused after failures
                      </span>
                    )}
                    <StatusBadge status={m.status} />
                    <span className="w-24 text-right tabular-nums text-muted-foreground">
                      {num(m.sent_today)} / {num(m.daily_limit)}
                    </span>
                    {m.last_error && <p className="w-full truncate text-xs text-destructive">{m.last_error}</p>}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Upcoming sends</CardTitle>
          </CardHeader>
          <CardContent>
            {d.upcoming.length === 0 ? (
              <EmptyState icon={CalendarClock} title="Nothing scheduled" description="Active campaigns will show their next sends here." />
            ) : (
              <ul className="divide-y text-sm">
                {d.upcoming.map((u, i) => (
                  <li key={i} className="flex items-center gap-3 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">{u.name || u.email}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {u.campaign} · step {u.step}
                      </div>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">{dateTime(u.at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
          </CardHeader>
          <CardContent>
            {d.recent_activity.length === 0 ? (
              <EmptyState icon={History} title="No activity yet" />
            ) : (
              <ul className="divide-y text-sm">
                {d.recent_activity.map((a) => (
                  <li key={a.id} className="flex items-start gap-3 py-2">
                    <div className="min-w-0 flex-1">
                      <Link href={`/leads/${a.contact_id}`} className="font-medium hover:underline">
                        {a.contact}
                      </Link>
                      <p className="truncate text-xs text-muted-foreground">{a.summary}</p>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">{relTime(a.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
