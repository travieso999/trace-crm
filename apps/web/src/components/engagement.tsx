"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CircleDot, ClipboardCheck, ClipboardPen, Eye, FileSignature, Flame, History, Mail, MailOpen, MessageSquareReply,
  MousePointerClick, PlayCircle, ShieldQuestion,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { EmptyState, ErrorState } from "@/components/common";
import { useCan } from "@/components/app-shell";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EngagementLevel, EngagementTimeline, TimelineItem } from "@/lib/types";

const ET = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
});

/** "Nov 16, 2026, 10:02 AM ET": the team works in Eastern time, whatever the browser's zone. */
export function etTime(iso: string | null | undefined) {
  return iso ? `${ET.format(new Date(iso))} ET` : "—";
}

export const LEVEL_LABEL: Record<EngagementLevel, string> = { high: "High", medium: "Medium", low: "Low" };
export const LEVEL_HELP: Record<EngagementLevel, string> = {
  high: "Verified demo interaction or a completed demo application",
  medium: "A tracked link clicked (not a security scanner), a demo visit, or a reply",
  low: "Email opened only (an estimate)",
};
const LEVEL_TONE: Record<EngagementLevel, string> = {
  high: "bg-signal/15 text-signal",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  low: "bg-muted text-muted-foreground",
};

export function LevelBadge({ level, className }: { level: EngagementLevel | null; className?: string }) {
  if (!level) return <span className="text-muted-foreground">—</span>;
  return (
    <Badge variant="secondary" title={LEVEL_HELP[level]} className={cn("gap-1 border-0 font-medium", LEVEL_TONE[level], className)}>
      {level === "high" && <Flame className="size-3" aria-hidden />}
      {LEVEL_LABEL[level]}
    </Badge>
  );
}

const ICONS: Record<TimelineItem["kind"], LucideIcon> = {
  email_sent: Mail, email_opened: MailOpen, email_click: MousePointerClick, demo_click: PlayCircle, demo_visit: Eye,
  demo_application_started: ClipboardPen, demo_application_completed: FileSignature, demo_form: ClipboardCheck,
  email_replied: MessageSquareReply,
};
const STRONG = new Set(["demo_click", "demo_application_completed", "email_replied"]);

export function EngagementTimelineView({ contactId }: { contactId: string }) {
  const canSeeRaw = useCan("outreach.access");
  const [raw, setRaw] = React.useState(false);
  const q = useQuery({
    queryKey: ["engagement-timeline", contactId, raw],
    queryFn: () => api<EngagementTimeline>(`/api/tracking/contacts/${contactId}/timeline`, { query: { raw: raw || undefined } }),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isPending) return <div className="grid gap-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}</div>;
  const { items, level } = q.data;
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Engagement</span>
          <LevelBadge level={level} />
        </div>
        {canSeeRaw && (
          <div className="flex items-center gap-2">
            <Switch id={`raw-${contactId}`} checked={raw} onCheckedChange={setRaw} />
            <Label htmlFor={`raw-${contactId}`} className="text-xs font-normal text-muted-foreground">Raw events</Label>
          </div>
        )}
      </div>
      {items.length === 0 ? (
        <EmptyState icon={History} title="No email engagement yet"
          description="Emails sent before click tracking was turned on can't show clicks or demo visits." />
      ) : (
        <ol className="relative grid gap-0" aria-label="Engagement timeline">
          {items.map((it, i) => {
            const Icon = ICONS[it.kind] ?? CircleDot;
            return (
              <li key={`${it.kind}-${it.at}-${i}`} className="relative flex gap-3 pb-4">
                {i < items.length - 1 && <span className="absolute top-7 bottom-0 left-[13px] w-px bg-border" aria-hidden />}
                <span className={cn("relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border bg-popover",
                  STRONG.has(it.kind) && "border-signal/50")}>
                  <Icon className={cn("size-3.5", STRONG.has(it.kind) ? "text-signal" : "text-muted-foreground")} aria-hidden />
                </span>
                <div className="grid min-w-0 gap-0.5 pt-0.5">
                  <p className="text-sm break-words">
                    {it.summary} <span className="text-muted-foreground">— {etTime(it.at)}</span>
                    {!!it.count && it.count > 1 && <span className="ml-1 text-xs text-muted-foreground">({it.count} clicks)</span>}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {it.campaign ?? "No campaign"}
                    {it.sample && " · left the demo's example answers"}
                    {it.url && <> · <span className="break-all">{it.url}</span></>}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {raw && q.data.raw && (
        <section aria-label="Raw events" className="grid gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Raw events ({q.data.raw.length})</h3>
          <ul className="grid gap-1.5 text-xs">
            {q.data.raw.map((e, i) => (
              <li key={i} className="rounded-md border px-2.5 py-1.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="font-medium">{e.source === "demo" ? `demo ${e.type}` : e.type}</span>
                  <span className="text-muted-foreground">{etTime(e.at)}</span>
                  {e.suspected_bot && !e.verified_human && (
                    <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400">
                      <ShieldQuestion className="size-3" aria-hidden /> likely automated: {(e.reasons ?? []).join(", ").replace(/_/g, " ")}
                    </span>
                  )}
                  {e.verified_human && <span className="text-emerald-700 dark:text-emerald-400">verified by demo activity</span>}
                </div>
                <div className="break-all text-muted-foreground">
                  {[e.link, e.page && `page ${e.page}`, e.form && `form ${e.form}`, e.step != null && `step ${e.step}`,
                    e.signed && "signed", e.user_agent].filter(Boolean).join(" · ")}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
